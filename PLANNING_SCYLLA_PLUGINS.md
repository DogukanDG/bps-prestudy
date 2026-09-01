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

## Plugin 3 — resource assignment (proposed)

Not in Samira's original two. It came out of measuring what was left after the
first two landed, and it is the cheapest of the three to build.

### Why

With both plugins in place, Scylla's mean cycle time on BPIC 2012 at 500 cases
is 3397 s against Prosimos's 6999 s -- a ratio of 0.49. Two measurements
attribute that:

**Eligibility, measured on Prosimos alone** (real eligibility vs. every resource
eligible for everything, so the engine is held constant):

| Prosimos | cycle time |
|---|---|
| real eligibility | 6684 |
| everyone eligible | 8112 |

Losing eligibility *raises* cycle time by 21%. Restoring it in Scylla would
therefore move 3397 up towards Prosimos, roughly to 4100 -- the right direction,
but not enough on its own.

**Selection policy**, isolated by giving every resource on an activity the same
duration, so the two engines' policies cannot differ in effect:

| | Scylla / Prosimos |
|---|---|
| all resources equally fast | 0.87 |
| real per-resource durations | 0.49 |

The drop from 0.87 to 0.49 is entirely how each engine chooses among available
resources. Prosimos takes the one that becomes free earliest, keeping busy
resources in the queue. Scylla takes one that is free *now*, so a fast resource
returns to the pool repeatedly and takes disproportionately more work.

So eligibility is the smaller term and selection policy is the larger one.

### What the source says

This is the cleanest of the three extension points.
`ResourceAssignmentPluggable` is consulted at the top of
`QueueManager.getResourcesForEvent()`:

```java
Optional<ResourceAssignmentPluggable> plugin = ResourceAssignmentPluggable.getInterestedPlugin(model, event);
if (plugin.isPresent()) return plugin.get().getResourcesForEvent(model, event).orElse(null);
```

A plugin that declares interest replaces Scylla's assignment entirely -- the
all-required-at-once semantics, the pool, and the free-right-now selection are
all bypassed. Both problems are fixable in one place, with **no core change**,
unlike plugin 1.

It would also remove the reason for pooling at all: with assignment under our
control, per-activity eligible sets can be honoured directly, and the shared
pool that inflates nothing but hides everything could go.

### The decision this raises

Restoring eligibility is uncontroversial -- it is information the model carries
and Scylla currently discards.

Matching Prosimos's selection rule is not. Two defensible readings:

- **Match it.** The claim is about running the same model on two engines.
  Resource selection is part of the model, not something an engine should decide
  arbitrarily, and leaving it unmatched means the comparison measures the
  engines' queueing internals rather than the model.
- **Leave it.** Scylla's policy is Scylla's. Overriding it makes the study
  "Scylla made to behave like Prosimos" rather than "Scylla".

This is a methodological question, not a technical one, and it belongs with
Samira before the plugin is written. A middle option exists: implement the
selection rule behind a flag and report both, which turns the disagreement into
a measurement rather than a choice.

### Steps

1. Plugin: 4 classes, no core change. Parse per-activity eligible resource sets;
   implement selection; return a `ResourceObjectTuple`.
2. Converter: emit eligible sets per activity; drop the shared pool once the
   plugin handles assignment.
3. Tests: an activity must only ever be performed by an eligible resource; with
   the Prosimos rule enabled, the realised duration distribution should match
   the declared resource means far more closely than the current 182 s against
   344 s.
4. Re-measure. Expect the ratio to move from 0.49 towards 0.87, which is what
   everything else accounts for.

**Estimate: 3–4 days**, most of it in the selection semantics and its tests.

---

## Order

**Plugins 1 and 2 are done.** Plugin 2 was written first, and that ordering held
up: it addressed the larger effect, needed no core change, and its result was
directly measurable, which made plugin 1's core change easier to justify
afterwards.

Plugin 3 is proposed rather than scheduled. It should not start until the
selection-policy question above is settled, because the answer changes what
gets built -- restoring eligibility alone is a smaller job than that plus
matching Prosimos's selection rule.

Worth noting the irony in hindsight: plugin 3 needs no core change and has the
cleanest extension point of the three, so if it had been on the original list it
would have been the natural place to start.

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
