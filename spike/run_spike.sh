#!/usr/bin/env bash
# Phase 1 spike runner.
#
#   ./run_spike.sh build              # build scylla.jar via Docker (once)
#   ./run_spike.sh run bpic2012 100   # one simulation at 100 cases
#   ./run_spike.sh bench bpic2012     # time 100/500/1000/3000 cases
#
# Answers the three Phase 1 questions: does the Simod BPMN parse, which KPIs
# does Scylla report, and how long does one simulation take.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCYLLA_SRC="${SCYLLA_SRC:-$HERE/../../SimuBridge/Scylla-Container/scylla}"
JAR="$HERE/scylla.jar"

build() {
    if [ ! -d "$SCYLLA_SRC" ]; then
        echo "Scylla source not found at: $SCYLLA_SRC" >&2
        echo "Clone it first:" >&2
        echo "  git clone https://github.com/bptlab/scylla.git" >&2
        echo "then re-run with SCYLLA_SRC=/path/to/scylla ./run_spike.sh build" >&2
        exit 1
    fi

    # The version bundled with SimuBridge predates Fix #72 (named resource
    # instance timetables being ignored), which is exactly the mechanism the
    # pooling strategy relies on. Build from current main instead.
    echo "== checking Scylla version =="
    git -C "$SCYLLA_SRC" fetch --quiet origin || true
    if git -C "$SCYLLA_SRC" merge-base --is-ancestor f9671cb HEAD 2>/dev/null; then
        echo "   HEAD includes Fix #72 - ok"
    else
        echo "   HEAD is missing Fix #72; checking out origin/main"
        git -C "$SCYLLA_SRC" checkout --quiet origin/main
    fi
    echo "   commit: $(git -C "$SCYLLA_SRC" rev-parse --short HEAD)"

    echo "== building scylla.jar (Docker, no local Maven needed) =="
    docker run --rm \
        -v "$(cd "$SCYLLA_SRC" && pwd)":/app \
        -w /app \
        maven:3.9-eclipse-temurin-17 \
        mvn -q package -DskipTests

    local built
    built="$(find "$SCYLLA_SRC/target" -maxdepth 1 -name '*.jar' ! -name '*sources*' | head -1)"
    [ -n "$built" ] || { echo "build produced no jar" >&2; exit 1; }
    cp "$built" "$JAR"
    [ -d "$SCYLLA_SRC/target/libs" ] && cp -r "$SCYLLA_SRC/target/libs" "$HERE/libs"
    echo "== ready: $JAR =="
}

run_one() {
    local ds="$1" cases="$2"
    [ -f "$JAR" ] || { echo "no scylla.jar - run './run_spike.sh build' first" >&2; exit 1; }

    python "$HERE/build_spike_config.py" --dataset "$ds" --cases "$cases"

    local out="$HERE/$ds/out_${cases}"
    # Scylla refuses to write into an existing directory (SimulationManager.java:179)
    # and uses mkdir(), not mkdirs() - so the parent must exist, the leaf must not.
    rm -rf "$out"

    echo "== running $ds at $cases cases =="
    local t0 t1
    t0=$(date +%s.%N)
    java -jar "$JAR" --headless --enable-bps-logging \
        --config="$HERE/$ds/global_config.xml" \
        --bpmn="$HERE/$ds/model.bpmn" \
        --sim="$HERE/$ds/sim_config.xml" \
        --output="$out" 2>&1 | tail -25
    t1=$(date +%s.%N)

    echo
    echo "== wall time: $(echo "$t1 - $t0" | bc) s =="
    echo "== output files =="
    find "$out" -type f -printf '   %-55p %10s bytes\n' 2>/dev/null | head -20 \
        || ls -la "$out"
}

bench() {
    local ds="${1:-bpic2012}"
    echo "cases,wall_seconds" > "$HERE/${ds}_timing.csv"
    for c in 100 500 1000 3000; do
        echo
        echo "############ $c cases ############"
        local t0 t1
        t0=$(date +%s.%N)
        run_one "$ds" "$c" >/dev/null 2>&1 || { echo "FAILED at $c cases"; continue; }
        t1=$(date +%s.%N)
        local d
        d=$(echo "$t1 - $t0" | bc)
        echo "$c,$d" >> "$HERE/${ds}_timing.csv"
        echo "$c cases -> ${d}s"
    done
    echo
    echo "== timing written to ${ds}_timing.csv =="
    cat "$HERE/${ds}_timing.csv"
}

case "${1:-}" in
    build) build ;;
    run)   run_one "${2:-bpic2012}" "${3:-100}" ;;
    bench) bench "${2:-bpic2012}" ;;
    *)     sed -n '2,9p' "$0" ;;
esac
