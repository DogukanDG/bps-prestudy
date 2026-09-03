# What we have done so far

A plain-language summary. The technical detail lives in
`backend/src/simulation_pipeline/simulation/scylla/README.md`.

---

## The goal

The sensitivity analysis pipeline uses Prosimos as its simulation engine. We
want to run the same analysis with a second engine, Scylla, and see whether it
identifies the same parameters as important. If it does, the method does not
depend on which simulator you use — which is worth reporting.

Both engines have to run the *same* model, so we needed to translate what Simod
discovered into the format Scylla reads.

---

## Step 1 — Built the translator

Simod produces a BPMN file and a JSON file describing durations, resources,
calendars and branching. Scylla wants two XML files. We wrote the converter that
turns one into the other.

It works: `engine="scylla"` now runs end to end and produces the same KPI table
the Prosimos side produces, so nothing downstream had to change.

## Step 2 — Checked it was correct

We stripped a model down to something where both engines *must* agree — fixed
durations, one resource always available, no randomness. Under those conditions
they produce identical results.

That matters because it separates two things: a mistake in our translation, and
a genuine difference between the engines. From here on, any disagreement is the
second kind.

Along the way this caught several real bugs in the translator, all now fixed.

## Step 3 — Found where the two engines disagreed

On the real models they did not agree: Scylla's average cycle time was 2.2x
Prosimos's. We traced it to two things Scylla simply does not have.

**Arrival calendars.** The model says cases only arrive during certain hours —
about 45% of the week. Prosimos respects that. Scylla has no such concept, so it
spread the same cases across all 168 hours. Cases arrived at night when nobody
was working and queued until morning.

**Resource-dependent durations.** Simod records a separate duration for each
resource — on one activity the fastest is 6.7 seconds and the slowest is over
1000. Scylla gives an activity a single duration regardless of who does it.

## Step 4 — Added both to Scylla as plugins

Samira checked with Leon, who develops Scylla, and confirmed both were missing
and both should be addable as plugins. She asked us to keep the original Simod
model and extend Scylla, rather than simplify the model to fit.

We wrote both.

| | result |
|---|---|
| Scylla, unmodified | 2.24x Prosimos |
| with arrival calendar | 0.84x |
| with both plugins | 0.49x |

The arrival calendar plugin closed most of the gap, as expected.

The duration plugin moved it the other way, and that turned out to be
interesting rather than wrong.

## Step 5 — Understood why the second plugin widened the gap

The two engines choose resources differently.

Prosimos picks the resource that will become free *earliest*, so a busy resource
stays in the queue and eventually gets its turn. Scylla picks one that is free
*right now*, so a fast resource finishes, returns to the pool, and gets picked
again — over and over.

While every resource had the same duration this made no difference. Once
durations differ per resource, fast resources in Scylla end up doing most of the
work, and the process runs faster than the model implies.

We measured it: give every resource the same speed and the two engines agree to
within 13%. Restore the real per-resource speeds and the gap opens to 51%. So
the gap is the selection rule, not the durations themselves.

---

## Where we are now

The translator works and is tested. Two plugins are written, measured and
committed. The remaining difference between the engines is understood and
attributable rather than mysterious.

## What is still open

**One more thing Scylla cannot express.** It does not know which resources are
allowed to perform which activity — currently every resource can do everything.
We measured the effect by removing that information from Prosimos instead: it
raises cycle time by about 21%.

**A question for Samira.** Both of these could be fixed with a third plugin, and
it would be the easiest of the three to write. But it raises a question we
cannot answer on our own: how far should we go in making Scylla behave like
Prosimos? Adding the eligibility information seems clearly right — the model
already contains it. Changing *how Scylla picks a resource* is different; past
some point we would be comparing "Scylla made to act like Prosimos" rather than
Scylla.

**The comparison itself.** The actual sensitivity analysis comparison — do both
engines rank the parameters the same way — has not been completed yet. The code
is ready; it needs an uninterrupted run.

---

## Two things worth mentioning to others

We found a defect in Scylla while writing the arrival plugin: a helper that
finds the next open time window can return one that already passed, which let
arrivals slip through outside the calendar. Our plugin works around it, but
resource calendars use the same helper, so Leon may want to know.

And SimuBridge, the existing Simod-to-Scylla bridge, cannot handle these models.
It assumes Simod was run in "pooled" mode, where resources are grouped into
roles. Ours were discovered in "differentiated" mode, where every resource is
its own profile. Its distribution mapping also reads the wrong parameter
indices — exponential durations come out as zero.
