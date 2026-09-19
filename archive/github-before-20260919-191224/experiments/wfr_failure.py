import argparse
import subprocess
import time
from contextlib import nullcontext

from pymongo import ReadPreference

from common import (
    DEFAULT_DB,
    DEFAULT_DIRECT_HOSTS,
    DEFAULT_URI,
    build_read_concern,
    build_write_concern,
    collection_for_client,
    collection_for_direct_host,
    discover_members,
    observe_dependency,
    parse_direct_hosts,
    replica_client,
    reset_collection,
    save_csv,
    secondary_hosts,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="MongoDB Writes-Follow-Reads Failure Experiment"
    )

    parser.add_argument("--write-concern", type=str, default="1")
    parser.add_argument("--read-concern", type=str, default="local")
    parser.add_argument("--scenario", type=str, default="failure")
    parser.add_argument("--iterations", type=int, default=1000)

    parser.add_argument("--uri", type=str, default=DEFAULT_URI)
    parser.add_argument(
        "--direct-hosts",
        type=str,
        default=DEFAULT_DIRECT_HOSTS,
    )
    parser.add_argument("--database", type=str, default=DEFAULT_DB)
    parser.add_argument(
        "--collection",
        type=str,
        default="wfr_collection",
    )

    parser.add_argument(
        "--causal-session",
        action="store_true",
        help=(
            "Run dependent read/write operations inside one "
            "causally consistent session."
        ),
    )

    parser.add_argument(
        "--after-v1-delay-ms",
        type=float,
        default=0,
        help="Delay after writing v1 before Client B reads it.",
    )

    parser.add_argument(
        "--after-v2-delay-ms",
        type=float,
        default=0,
        help="Delay after writing v2 before observing a secondary.",
    )

    return parser.parse_args()


# ==========================================================
# Find the current PRIMARY dynamically.
# Do NOT assume mongo1 is always PRIMARY.
# ==========================================================
def get_primary_container():
    nodes = [
        ("mongo1", "27017"),
        ("mongo2", "27018"),
        ("mongo3", "27019"),
    ]

    for container, port in nodes:
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "mongosh",
                    "--port",
                    port,
                    "--quiet",
                    "--eval",
                    "print(db.hello().isWritablePrimary)",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.stdout.strip() == "true":
                return container

        except Exception:
            pass

    return None


def stop_container(container):
    subprocess.run(
        ["docker", "stop", container],
        check=True,
    )


def start_container(container):
    subprocess.run(
        ["docker", "start", container],
        check=True,
    )


def main():
    args = parse_args()

    wc = build_write_concern(args.write_concern)
    rc = build_read_concern(args.read_concern)

    client = replica_client(args.uri)
    client.admin.command("ping")

    members = discover_members(
        parse_direct_hosts(args.direct_hosts)
    )

    secondaries = secondary_hosts(members)

    if not secondaries:
        raise RuntimeError(
            "Writes-Follow-Reads requires at least one SECONDARY node."
        )

    reset_collection(
        client,
        args.database,
        args.collection,
    )

    # ------------------------------------------------------
    # Client A / B write through the replica-set PRIMARY.
    # ------------------------------------------------------
    writer_collection = collection_for_client(
        client,
        args.database,
        args.collection,
        write_concern=wc,
        read_preference=ReadPreference.PRIMARY,
    )

    # ------------------------------------------------------
    # Client B reads prerequisite v1 from PRIMARY.
    # ------------------------------------------------------
    primary_reader = collection_for_client(
        client,
        args.database,
        args.collection,
        read_concern=rc,
        read_preference=ReadPreference.PRIMARY,
    )

    # ------------------------------------------------------
    # Used when causal session is enabled.
    # ------------------------------------------------------
    session_secondary_reader = collection_for_client(
        client,
        args.database,
        args.collection,
        read_concern=rc,
        read_preference=ReadPreference.SECONDARY,
    )

    # ------------------------------------------------------
    # Directly inspect individual SECONDARY nodes
    # in weak configuration.
    # ------------------------------------------------------
    direct_secondary_readers = [
        (
            host,
            *collection_for_direct_host(
                host,
                args.database,
                args.collection,
                rc,
            ),
        )
        for host in secondaries
    ]

    print("=== 启动 Writes-Follow-Reads Failure 实验 ===")
    print(
        f"配置: WriteConcern={args.write_concern}, "
        f"ReadConcern={args.read_concern}"
    )
    print(
        f"场景: {args.scenario}, "
        f"迭代次数: {args.iterations}"
    )
    print(f"Causal Session: {args.causal_session}")
    print(
        f"Initial Secondary targets: "
        f"{', '.join(secondaries)}"
    )

    print("\n故障计划:")
    print("Iteration 1-199   : 正常运行")
    print("Iteration 200     : 停止当前 PRIMARY")
    print("Iteration 201-599 : Primary failure 状态")
    print("Iteration 600     : 恢复被停止节点")
    print("Iteration 601-1000: 恢复后运行")
    print()

    rows = []
    violations = 0

    # Remember which node we intentionally stopped.
    failed_node = None

    session_context = (
        client.start_session(causal_consistency=True)
        if args.causal_session
        else nullcontext(None)
    )

    try:
        with session_context as session:

            for i in range(1, args.iterations + 1):

                # ==================================================
                # Fields used to record a fault/recovery event.
                # Normally these are empty.
                # ==================================================
                scenario_event = ""
                target_node = ""
                event_time = ""

                # ==================================================
                # ITERATION 200:
                # Dynamically discover and stop current PRIMARY.
                # ==================================================
                if (
                    args.scenario == "failure"
                    and i == 200
                ):
                    failed_node = get_primary_container()

                    if failed_node is None:
                        print(
                            f"\n[Iteration {i}] "
                            "ERROR: Cannot determine "
                            "current PRIMARY."
                        )

                        scenario_event = (
                            "primary_detection_failed"
                        )
                        event_time = time.time()

                    else:
                        scenario_event = "primary_stop"
                        target_node = failed_node
                        event_time = time.time()

                        print(
                            f"\n[Iteration {i}] "
                            f"Stopping current PRIMARY: "
                            f"{failed_node}"
                        )

                        stop_container(failed_node)

                        print(
                            f"[Iteration {i}] "
                            f"{failed_node} stopped."
                        )

                        print(
                            "Waiting 10 seconds for "
                            "replica-set election..."
                        )

                        time.sleep(10)

                        new_primary = get_primary_container()

                        if new_primary is None:
                            print(
                                "WARNING: No PRIMARY detected "
                                "after waiting 10 seconds."
                            )
                        else:
                            print(
                                f"Failover completed. "
                                f"New PRIMARY: {new_primary}"
                            )

                # ==================================================
                # ITERATION 600:
                # Restart the node that failed at iteration 200.
                # ==================================================
                if (
                    args.scenario == "failure"
                    and i == 600
                ):
                    if failed_node is not None:

                        scenario_event = "primary_restart"
                        target_node = failed_node
                        event_time = time.time()

                        print(
                            f"\n[Iteration {i}] "
                            f"Restarting failed node: "
                            f"{failed_node}"
                        )

                        start_container(failed_node)

                        print(
                            f"[Iteration {i}] "
                            f"{failed_node} restarted."
                        )

                        print(
                            "Waiting 10 seconds for the node "
                            "to rejoin the replica set..."
                        )

                        time.sleep(10)

                        current_primary = (
                            get_primary_container()
                        )

                        if current_primary is None:
                            print(
                                "WARNING: No PRIMARY detected "
                                "after recovery."
                            )
                        else:
                            print(
                                f"Current PRIMARY after "
                                f"recovery: "
                                f"{current_primary}"
                            )

                # ==================================================
                # Original WFR experiment starts here.
                # ==================================================

                prerequisite_id = f"v1:{i}"
                dependent_id = f"v2:{i}"

                success = True
                error = ""

                read_v1 = False
                v1_visible_on_observer = False
                v2_visible_on_observer = False
                is_violation = False
                observer_check_order = "dependent_then_prerequisite"

                # Alternate between initially discovered
                # secondary nodes.
                read_host, _, direct_read_collection = (
                    direct_secondary_readers[
                        (i - 1)
                        % len(direct_secondary_readers)
                    ]
                )

                start_time = time.time()

                try:
                    # ----------------------------------------------
                    # Step 1:
                    # Client A writes prerequisite event v1.
                    # ----------------------------------------------
                    writer_collection.insert_one(
                        {
                            "_id": prerequisite_id,
                            "iteration": i,
                            "type": "prerequisite",
                            "version": 1,
                            "writer": "client_A",
                            "created_at": time.time(),
                        },
                        session=session,
                    )

                    if args.after_v1_delay_ms > 0:
                        time.sleep(
                            args.after_v1_delay_ms / 1000
                        )

                    # ----------------------------------------------
                    # Step 2:
                    # Client B reads prerequisite v1.
                    # ----------------------------------------------
                    prerequisite = primary_reader.find_one(
                        {"_id": prerequisite_id},
                        session=session,
                    )

                    read_v1 = prerequisite is not None

                    # B must observe v1 before producing v2.
                    if not read_v1:
                        raise RuntimeError(
                            f"Client B could not read "
                            f"prerequisite "
                            f"{prerequisite_id}"
                        )

                    # ----------------------------------------------
                    # Step 3:
                    # Client B writes v2 after reading v1.
                    # v2 explicitly records dependency on v1.
                    # ----------------------------------------------
                    writer_collection.insert_one(
                        {
                            "_id": dependent_id,
                            "iteration": i,
                            "type": "dependent",
                            "version": 2,
                            "writer": "client_B",
                            "depends_on": (
                                prerequisite_id
                            ),
                            "created_at": time.time(),
                        },
                        session=session,
                    )

                    if args.after_v2_delay_ms > 0:
                        time.sleep(
                            args.after_v2_delay_ms / 1000
                        )

                    # ----------------------------------------------
                    # Step 4:
                    # Observe a SECONDARY.
                    #
                    # Weak:
                    # direct read from one secondary.
                    #
                    # Causal:
                    # secondary read preference inside
                    # causal session.
                    # ----------------------------------------------
                    observer_collection = (
                        session_secondary_reader
                        if args.causal_session
                        else direct_read_collection
                    )

                    observer_target = (
                        "session_secondary"
                        if args.causal_session
                        else read_host
                    )

                    observed_v1, observed_v2, observer_check_order = (
                        observe_dependency(
                            observer_collection,
                            prerequisite_id,
                            dependent_id,
                            session=session,
                        )
                    )

                    v1_visible_on_observer = (
                        observed_v1 is not None
                    )

                    v2_visible_on_observer = (
                        observed_v2 is not None
                    )

                    # ----------------------------------------------
                    # WFR violation:
                    #
                    # Observer sees dependent write v2
                    # but cannot see prerequisite v1.
                    # ----------------------------------------------
                    is_violation = (
                        v2_visible_on_observer
                        and not v1_visible_on_observer
                    )

                    if is_violation:
                        violations += 1

                except Exception as exc:
                    # Important:
                    # a temporary operation failure during
                    # failover is recorded instead of ending
                    # the entire 1000-iteration experiment.
                    success = False
                    error = str(exc)

                    observer_target = (
                        "session_secondary"
                        if args.causal_session
                        else read_host
                    )

                    print(
                        f"第 {i} 次 WFR 实验失败: "
                        f"{exc}"
                    )

                latency_ms = (
                    time.time() - start_time
                ) * 1000

                rows.append(
                    {
                        "timestamp": time.time(),
                        "iteration": i,
                        "client_id": "client_wfr",
                        "operation": (
                            "READ_V1_WRITE_V2_"
                            "READ_SECONDARY"
                        ),
                        "target": observer_target,
                        "prerequisite_id": (
                            prerequisite_id
                        ),
                        "dependent_id": dependent_id,
                        "client_b_read_v1": read_v1,
                        "v1_visible_on_observer": (
                            v1_visible_on_observer
                        ),
                        "v2_visible_on_observer": (
                            v2_visible_on_observer
                        ),
                        "observer_check_order": (
                            observer_check_order
                        ),
                        "read_concern": (
                            args.read_concern
                        ),
                        "write_concern": (
                            args.write_concern
                        ),
                        "causal_session": (
                            args.causal_session
                        ),
                        "scenario": args.scenario,

                        # Fault injection information
                        "scenario_event": (
                            scenario_event
                        ),
                        "target_node": target_node,
                        "event_time": event_time,

                        "latency_ms": round(
                            latency_ms,
                            2,
                        ),
                        "success": success,
                        "violation": is_violation,
                        "error": error,
                    }
                )

    finally:
        for (
            _,
            direct_reader_client,
            _,
        ) in direct_secondary_readers:
            direct_reader_client.close()

        client.close()

        # Safety:
        # if the program exits unexpectedly after stopping
        # a node but before iteration 600, try to restart it.
        if failed_node is not None:
            try:
                result = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.Running}}",
                        failed_node,
                    ],
                    capture_output=True,
                    text=True,
                )

                if result.stdout.strip() == "false":
                    print(
                        f"\nSafety recovery: "
                        f"restarting {failed_node}..."
                    )

                    start_container(failed_node)

                    print(
                        f"{failed_node} restarted."
                    )

            except Exception as exc:
                print(
                    f"WARNING: automatic safety "
                    f"recovery failed: {exc}"
                )

    violation_rate = (
        (violations / len(rows)) * 100
        if rows
        else 0
    )

    success_count = sum(
        1
        for row in rows
        if row["success"]
    )

    failure_count = (
        len(rows) - success_count
    )

    print("\n==============================")
    print("实验完成!")
    print("==============================")

    print(
        f"WFR 违背次数: "
        f"{violations}/{len(rows)} "
        f"(违背率: {violation_rate:.2f}%)"
    )

    print(
        f"成功操作: "
        f"{success_count}/{len(rows)}"
    )

    print(
        f"失败操作: "
        f"{failure_count}/{len(rows)}"
    )

    filename = (
        "results/raw/"
        f"wfr_{args.scenario}"
        f"_w{args.write_concern}"
        f"_r{args.read_concern}"
        f"_causal"
        f"{str(args.causal_session).lower()}"
        ".csv"
    )

    save_csv(rows, filename)

    print(
        f"日志已保存至: {filename}"
    )


if __name__ == "__main__":
    main()
