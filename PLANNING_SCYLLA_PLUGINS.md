# Two Scylla plugins — feasibility and plan

> **Status 2026-09-01: both plugins are written and measured.** The feasibility
> assessment below held up, with one correction: option C for plugin 1 was
> checked and is genuinely impossible (both resource-assignment paths run before
> TaskBeginEvent, and its plugin hook fires after the duration is sampled), so
> the small core change was made. Results and what they mean are in
> `backend/src/simulation_pipeline/simulation/scylla/README.md` -- in short, the
> arrival calendar plugin closed the dominant gap as predicted, and the
> resource-duration plugin widened it, for a reason worth reading.

Samira's decision (2026-09-01): keep the differentiated Simod output and extend
Scylla to fit the model, rather than pooling the model to fit Scylla. Leon
confirmed both behaviours are missing and said plugins should be able to add
them.

This plan is written after reading the Scylla source rather than from the
plugin documentation, because the two plugins turn out **not** to be
symmetrical: one fits the existing extension points, the other does not.

---

## Why these two

Measured on BPIC 2012, the gap between the engines decomposes as:

| Source | Effect on cycle time |
|---|---|
| Arrival calendar missing in Scylla | ~+130% |
| Resource pooling in our converter | ~+34% |
| Everything else (discretisation, calendars, capacity) | ~0 |

Plugin 2 addresses the first, plugin 1 the second. Together they would remove
the need for pooling entirely and let both engines run the same model.

---

## Plugin 1 — resource-dependent task durations

**Goal.** An activity's duration should come from the distribution belonging to
the resource that actually performs it, as Simod discovered it (up to 42
distributions per activity, fastest to slowest over 100x apart).

### What the source says

The good news: the assigned resource *is* known when the duration is drawn.
In `TaskBeginEvent.eventRoutine()`:

```java
double duration = pSimComponents.getDistributionSample(nodeId);        // line 87
...
ResourceObjectTuple tuple = processInstance.getAssignedResources().get(source);  // line 94
```

Seven lines apart. The information is in scope; it simply is not consulted.

The complication: neither the sampling call nor the plugin hook can use it.

- `getDistributionSample(Integer nodeId)` takes only a node id
  (`ProcessSimulationComponents.java:209`). There is no resource parameter and
  no overload.
- `TaskBeginEventPluggable.runPlugins()` fires at line 104 — *after* the
  duration is sampled, the terminate event is constructed and the time span is
  computed. A plugin there cannot change the duration.

So unlike the arrival plugin, this one cannot be done from an existing
extension point alone.

### Options

**A. Small core change plus plugin.** Add a resource-aware overload
(`getDistributionSample(nodeId, resourceIds)`) that consults a plugin-provided
map and falls back to current behaviour when none exists, and have
`TaskBeginEvent` pass the already-available tuple. Roughly a 10-line change to
two core files; everything else lives in the plugin.

**B. Move the plugin hook earlier.** Run `TaskBeginEventPluggable` before
sampling and let a plugin write the duration into the event. Fewer new methods,
but it changes the ordering for any existing plugin using that hook — a wider
blast radius than A for no real gain.

**C. Plugin only, no core change.** Not possible as far as I can tell. Worth
one more pass over `ScyllaEventPluggable` before committing to A, since being
wrong here is cheap to check and expensive to assume.

**Recommendation: A**, contingent on that check. It keeps the change minimal
and upstreamable, and Leon may well accept it — it makes Scylla strictly more
expressive without altering default behaviour.

### XML

Scylla's `<duration>` holds one distribution, so we need our own element,
parsed by the plugin (the pattern `eventArrivalRate` already uses):

```xml
<bsim:task id="...">
  <bsim:duration timeUnit="SECONDS">...</bsim:duration>   <!-- fallback -->
  <bsim:resourceDurations>
    <bsim:resourceDuration resourceId="10125" timeUnit="SECONDS">
      <bsim:arbitraryFiniteProbabilityDistribution>...</...>
    </bsim:resourceDuration>
  </bsim:resourceDurations>
</bsim:task>
```

Keeping `<duration>` as a fallback means a Scylla build without the plugin
still runs the model, just with pooled durations — useful for comparison and
for anyone else reading the config.

### Steps

1. Confirm option C really is impossible (half a day).
2. Core: resource-aware sampling overload, defaulting to today's behaviour.
3. Plugin: 4 classes on the `eventArrivalRate` template (~225 lines there).
4. Converter: emit `<resourceDurations>`; keep pooling behind a flag so the two
   can be compared.
5. Tests: T1 determinism must still pass; add a case where two resources have
   very different fixed durations and assert the observed durations follow the
   assignment.

**Estimate: 3–4 days.** Lower if C turns out to be possible.

---

## Plugin 2 — arrival calendar

**Goal.** Cases should only arrive during the hours the Simod
`arrival_time_calendar` covers (75 h/week, ~45%, on BPIC 2012).

### What the source says

This one fits cleanly, for two reasons.

**The hook is in the right place.** `ProcessInstanceGenerationEvent` runs
`ProcessInstanceGenerationEventPluggable.runPlugins()` at line 70, *before* the
next arrival is scheduled at line 89. A plugin can adjust the time span.

**The calendar logic already exists.** `DateTimeUtils` has `isWithin()`,
`getTimeTableIndexWithinOrNext()` and `getAvailabilityTime()`, which computes
exactly "given a timetable and a time, when does this actually become
available". Resource calendars already use it. Nothing new to write — the
plugin applies existing machinery to arrivals instead of resources.

### The one real decision

What happens to a case whose draw lands outside the calendar?

- **Defer** to the next open window. Preserves the case count; slightly
  distorts the inter-arrival distribution by clustering arrivals at window
  openings.
- **Drop** it. Preserves the inter-arrival distribution; loses cases, so the
  simulated volume no longer matches `processInstances`.

Prosimos compresses the same number of cases into the open hours, so **defer**
is the closer match and keeps the case count comparable across engines. This
should be stated explicitly in the write-up either way — it is a semantic
choice, not an implementation detail.

### XML

```xml
<bsim:startEvent id="...">
  <bsim:arrivalRate timeUnit="SECONDS">...</bsim:arrivalRate>
  <bsim:arrivalCalendar>
    <bsim:timetableItem from="MONDAY" to="MONDAY"
                        beginTime="05:00:00" endTime="08:00:00"/>
  </bsim:arrivalCalendar>
</bsim:startEvent>
```

Same `timetableItem` shape the global config already uses, so the parsing can
be borrowed rather than rewritten.

### Steps

1. Plugin: 4 classes, reusing `DateTimeUtils.getAvailabilityTime()`.
2. Converter: emit `<arrivalCalendar>` from `arrival_time_calendar`.
3. Tests: with a narrow calendar, no case may start outside it; case count must
   still match `processInstances`; a 24/7 calendar must reproduce current
   behaviour exactly.
4. Re-measure the engine gap — this is the one that should move the number.

**Estimate: 3–4 days**, most of it in the deferral semantics and its tests
rather than the code.

---

## Order

**Plugin 2 first**, despite plugin 1 being the one Leon called easy.

Three reasons:

- It addresses the larger effect (~130% against ~34%).
- It needs no core change, so it is the cleaner proof that the plugin route
  works at all.
- Its result is directly measurable: rerun `compare_engines.py` and see whether
  the gap closes. Plugin 1's effect is harder to see while the arrival calendar
  is still distorting everything.

If plugin 2 lands and the gap closes as predicted, plugin 1 becomes a
refinement rather than a necessity — and its core change is easier to justify
with that evidence in hand.

---

## Risks

| Risk | Response |
|---|---|
| Option C possible after all, making the core change unnecessary | Check first, it costs half a day |
| Leon rejects the core change for plugin 1 | Fall back to option B, or keep the fork local and document it |
| Deferral changes the arrival distribution more than expected | Measure the realised inter-arrival distribution against Prosimos; T3's Wasserstein tooling already does this |
| Plugin behaviour differs between our build and upstream | Pin the Scylla commit; already required for `f9671cb` |

## What this does not fix

`is_tasks_resources` as an SA dimension. Plugin 1 restores per-resource
durations, so the parameter space no longer collapses — but whether the
dimension becomes measurable again depends on the SA code treating each
(activity, resource) pair as a parameter, which is worth confirming before
claiming it.

## Still open, unrelated to the plugins

The SA ranking comparison has not produced a valid result yet. Two runs were
invalidated by JVM memory failures (now fixed), and the reruns were interrupted.
That measurement is what tells us whether the level differences reorder the
parameters at all — worth completing regardless of which plugin comes first,
since it sets how much the gap actually matters.
