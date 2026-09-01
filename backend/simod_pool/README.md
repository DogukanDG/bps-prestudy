# Simod in pool mode — prepared, not yet run

`2012_pool.yml` is `example_simod_inputs/BPIC_2012/2012_diff.yml` with one line
changed:

    discovery_type: differentiated  ->  discovery_type: pool

## Why this exists

The BPIC models were discovered in differentiated mode, so each resource is its
own profile and each activity carries a separate duration distribution per
resource. Scylla cannot represent that: an activity has a single duration, and
the resources listed under it are treated as all required at once, so writing
27 profiles for one activity would deadlock. The converter works around it by
pooling, which costs about 34% on cycle time.

Pool mode would give Scylla what it expects natively -- a profile becomes a
role with a quantity, and one `<resource>` entry per role is enough. That is
what SimuBridge relies on.

## What it would and would not fix

Fixes the pooling loss. Does **not** fix the arrival calendar, which Scylla has
no concept of at all and which accounts for the larger part of the divergence.

## The cost

Re-running Simod produces new model files. The existing Prosimos results were
computed on the current models, so they would no longer be a valid reference
and that arm would have to be re-run too. The replication claim weakens.

Whether the BPMN itself changes is untested. `control_flow` and `resource_model`
are separate config sections, so in principle only the latter is affected -- but
control-flow discovery is an optimisation over epsilon/eta with 30 iterations,
so identical output is not guaranteed. Either way the parameters.json changes,
which is what invalidates the reference.

## Running it

    conda activate bps
    cd backend
    python -c "..."   # see src/simod/run_simod.py for the invocation

Full config is 150 control-flow + 200 resource-model evaluations, each
discovering and simulating a model -- hours on a laptop. For a first look at
what pool mode produces, drop both num_iterations to 5.
