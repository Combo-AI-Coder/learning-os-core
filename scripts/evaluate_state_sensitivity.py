"""Offline test-only packet export and scoring for the Core #34 synthetic slice.

No model/network call, teaching policy, state write or Runtime operation is exposed.
Consumer sessions must be created fresh by an independently bounded runner.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.state_sensitivity_fixture import assess, cases, packet, packet_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=sorted(cases()))
    parser.add_argument("--arm", choices=("structured", "summary", "ablated"), default="structured")
    parser.add_argument("--iteration", choices=("pilot_v1", "application_v2"), default="application_v2")
    parser.add_argument("--contract-version", type=int, choices=(1, 2), default=2)
    parser.add_argument("--run-id", help="Recorded opaque run identity for review binding")
    parser.add_argument("--semantic-review", type=Path, help="Separate reviewed behavior/grounding disposition")
    parser.add_argument("--response", type=Path, help="Grade a previously returned JSON object; otherwise export only the blind packet")
    args = parser.parse_args()
    spec = cases()[args.case]
    payload = packet(spec, args.arm, args.contract_version, args.iteration)
    if args.response is None:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    try:
        result = json.loads(args.response.read_text(encoding="utf-8"))
        semantic_review = None if args.semantic_review is None else json.loads(args.semantic_review.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        # Do not echo input, filesystem details, or a false success on an
        # unparseable run. The caller retains the raw attempted response.
        print(json.dumps({"run_id": args.run_id, "packet_sha256": packet_hash(payload),
                          "behavioral_acceptance": "not_established", "error": "input_unreadable_or_invalid_json",
                          "error_type": type(exc).__name__, "learner_benefit": "not_measured"}))
        return 2
    assessment = assess(spec, result, args.arm, semantic_review, run_id=args.run_id, input_sha256=packet_hash(payload))
    print(json.dumps({"case": args.case, "arm": args.arm, "iteration": args.iteration,
                      "output_contract_version": args.contract_version, "packet_sha256": packet_hash(payload),
                      **assessment}, indent=2))
    return assessment["behavioral_acceptance"] != "bounded_pass"


if __name__ == "__main__":
    raise SystemExit(main())
