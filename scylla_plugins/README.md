# Scylla plugins — source patches

Two plugins written against `bptlab/scylla`, kept here as patches because that
repository is not ours to push to. The working tree they came from is a local
clone; these files are the durable copy.

| Patch | What it adds |
|---|---|
| `0001-Add-an-arrival-calendar-plugin.patch` | Restricts case arrivals to the hours the Simod arrival calendar covers |
| `0002-Add-a-resource-dependent-task-duration-plugin.patch` | Uses the duration distribution of the resource actually performing a task |

The commit messages carry the reasoning and the measurements; the adapter
README (`backend/src/simulation_pipeline/simulation/scylla/README.md`) has the
results and what they mean for the comparison.

## Rebuilding from these

```bash
git clone https://github.com/bptlab/scylla.git
cd scylla
git checkout origin/main          # must include f9671cb (Fix #72)
git am /path/to/scylla_plugins/*.patch

# Java 11: current Scylla targets source/target 11
docker run --rm -v "$PWD":/app -w /app maven:3.8-openjdk-11 \
  sh -c "mvn -q clean && mvn -q package -DskipTests"

cp target/scylla-*.jar /path/to/bps_clean/spike/scylla.jar
```

Two invocations of Maven: the jars in `lib/` are installed by `install-file`
goals bound to the `clean` phase, so a single `mvn package` cannot resolve them.

## Scope of the change

Patch 0001 is plugin-only — three new classes and one line in `plugins_list`.

Patch 0002 also touches two core files, because a plugin alone cannot do it:

- `ProcessSimulationComponents` gains `getDistributionSample(nodeId, resourceIds)`
  and a registry a plugin fills. With an empty registry it delegates to the
  existing single-argument method, so nothing changes for models without the new
  element.
- `TaskBeginEvent` passes the resource tuple it already holds.

Both are additive. A model without `<resourceDurations>` or `<arrivalCalendar>`
behaves exactly as before.

## Worth reporting upstream

`DateTimeUtils.getTimeTableIndexWithinOrNext` ranks candidate windows with
`getNextOrSameZonedDateTime`, which can return a window whose start already
passed earlier the same day. The resulting negative duration makes the helper
pick a window in the past. The arrival calendar plugin computes its own delay to
avoid this, but **resource calendars use the same helper**, so the same weakness
may affect them.

Observed while developing patch 0001: arrivals at 21:00 and 22:00 on Mondays
against a calendar closing at 20:00.
