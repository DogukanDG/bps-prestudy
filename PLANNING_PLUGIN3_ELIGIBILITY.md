# Plugin 3 — resource eligibility

Restore which resources may perform which activity, which is the last thing
Scylla cannot express from the Simod model.

Written after reading the Scylla source. One assumption from the earlier plan
turned out to be wrong and is corrected below.

---

## Why

With the first two plugins in place, Scylla's mean cycle time on BPIC 2012 at
500 cases is 3397 s against Prosimos's ~7000 — a ratio of 0.49. Breaking it
down:

| | Prosimos | Scylla |
|---|---|---|
| equal speeds — cycle | 9314 | 8187 |
| equal speeds — waiting | 2887 | 1505 |
| real speeds — cycle | 8267 | 3397 |
| real speeds — waiting | 3914 | **422** |

Processing time holds a roughly constant ratio, which is the known
metric-definition difference. What moves is waiting: Scylla barely queues once
resources differ in speed.

The cause is the shared pool. Prosimos limits each activity to the resources
Simod recorded for it — 27, 40, 42, 38, 42 and 2 of the 47. Scylla's single pool
lets every activity use all 47, so there is more effective capacity and less
contention. With equal speeds that costs about 10%; with real speeds the fast
resources become reachable from every activity at once and the queue collapses.

**Not a selection-rule difference.** Tested directly with two resources on one
activity, 10 s and 100 s, both always available: Prosimos gives the fast one 91%
of executions, Scylla 91%. Identical. Both let a fast resource cycle back and
take most of the work — a property of any availability-based queue. An earlier
version of the plan claimed otherwise, having read the two implementations
rather than measured them.

So there is no methodological question here. Restoring eligibility returns
information the model carries; it does not make Scylla imitate Prosimos.

---

## Why Scylla cannot express this itself

In BPIC 2012 the 47 resources form 13 groups by capability — 22 and 12 people in
the two largest, the rest mostly single-person. Structurally that is a clean fit
for Scylla's role-based `dynamicResource`.

The problem is that the groups overlap rather than partition. One activity can
be performed by up to nine of them, and Scylla reads

```xml
<resources>
  <resource id="Group1" amount="1"/>
  <resource id="Group2" amount="1"/>
</resources>
```

as *all of these are required simultaneously*, not *any one of these*. Listing
nine groups deadlocks the process.

Per-activity pools avoid the deadlock but break capacity instead: a resource in
five activities becomes five independent instances, so total capacity was 191
rather than 47 (measured in T1). Scylla can represent correct capacity or
eligibility, not both.

---

## The extension point

`ResourceAssignmentPluggable` is consulted at the top of
`QueueManager.getResourcesForEvent()`:

```java
Optional<ResourceAssignmentPluggable> plugin = ResourceAssignmentPluggable.getInterestedPlugin(model, event);
if (plugin.isPresent()) return plugin.get().getResourcesForEvent(model, event).orElse(null);
```

A plugin that declares interest replaces assignment entirely — the
all-required-at-once semantics, the pool and Scylla's own selection are all
bypassed. The plugin returns a `ResourceObjectTuple`, or empty to mean "nothing
available, queue this event".

### The complication found while reading

`QueueManager.resourceObjects` — the map of resource queues — is **private with
no accessor**. `SimulationModel.getResourceManager()` returns the QueueManager,
but a plugin cannot reach the queues inside it, so it cannot see which resources
are currently free, nor remove one from the queue when it takes it, nor put it
back.

This is the one real obstacle, and it means the plugin cannot be written without
touching the core after all. Three ways round it, in order of preference:

**A. Add a narrow accessor.** A public method on QueueManager along the lines of
`pollAvailable(Collection<String> resourceIds, TimeInstant now)` that polls only
the named resources and returns what is free. Small, additive, keeps the queue
bookkeeping inside QueueManager where it belongs, and does not expose the
internal map. Probably acceptable upstream.

**B. Make `getResourceObjects()` public.** Less code but a wider surface, and it
would let any plugin corrupt the queues.

**C. Keep our own parallel bookkeeping in the plugin.** No core change, but the
plugin would have to track availability itself and stay in sync with
QueueManager — duplicated state, and the kind of thing that goes subtly wrong.
Rejected unless A and B are both refused.

**Recommendation: A**, with the same shape as the change made for plugin 1 —
additive, default behaviour untouched.

---

## XML

Eligibility per activity, listing the resources rather than pools, since the
plugin does not use Scylla's resource mechanism at all:

```xml
<bsim:task id="...">
  <bsim:duration timeUnit="SECONDS">...</bsim:duration>
  <bsim:resourceDurations>...</bsim:resourceDurations>
  <bsim:eligibleResources>
    <bsim:eligibleResource resourceId="10125"/>
    <bsim:eligibleResource resourceId="10138"/>
  </bsim:eligibleResources>
</bsim:task>
```

Verbose — 27 to 42 entries per activity — but explicit, and it avoids inventing
a grouping the model does not have. The existing `<resources>` element stays as
the fallback for a build without the plugin.

---

## Steps

1. **Core**: add the polling accessor to QueueManager (option A). Nothing else
   changes.
2. **Plugin**: 3 classes — utils, SC parser for `<eligibleResources>`, and the
   assignment plugin itself. It declares interest only for activities that have
   an eligibility list, so anything else falls through to Scylla's own logic.
3. **Selection**: among the eligible and available, keep Scylla's existing rule
   (longest since last used). Measured above to behave the same as Prosimos, so
   there is nothing to decide here.
4. **Converter**: emit `<eligibleResources>` per activity. Keep the shared pool
   in the global config — it still defines the resource instances and their
   calendars; the plugin only changes who may be drawn from it.
5. **Tests**: an activity must never be performed by an ineligible resource;
   capacity must stay 47; T1 determinism must still pass; the single-resource
   case must still serialise.
6. **Measure**: rerun the comparison. Expect the ratio to move from 0.49 back
   towards 1.0. If it does not, something else is in play and this plan's
   attribution was incomplete.

**Estimate: 2–3 days.** Less than the other two, since there is no new
distribution handling and no calendar arithmetic.

---

## Risks

| Risk | Response |
|---|---|
| The accessor is refused upstream | Fall back to B, or keep the fork local — it is already the case for plugin 1 |
| Selecting one resource breaks `sharedTimetable` | Only one resource is ever chosen, so the tuple's timetable is that resource's own; no intersection needed (`QueueManager:392` does the same for the first object) |
| Queue bookkeeping drifts | Do the polling inside QueueManager rather than the plugin, which is the point of option A |
| The ratio does not return to ~1.0 | Then the attribution is incomplete. Two hypotheses have already been wrong this way; measure before claiming |

## What this will not fix

The metric definitions. `processing_time` and `waiting_time` are measured
differently by the two engines and this plugin does not touch that.
`cycle_time` is unaffected and remains the safe comparison.
