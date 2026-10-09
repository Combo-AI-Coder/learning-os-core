"""Export/re-score frozen diagnostic repair packets; never calls a provider."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.diagnostic_repair_fixture import assess_repair, prospective_packet, prospective_spec
from tests.state_sensitivity_fixture import packet_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", choices=("q00", "q10", "q01", "q02", "q03", "h01", "h02", "h03", "h04", "h05", "h06"))
    parser.add_argument("--response", type=Path)
    parser.add_argument("--semantic-review", type=Path)
    args = parser.parse_args()
    try:
        value = prospective_packet(args.run_id)
        if args.response is None:
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
        response = json.loads(args.response.read_text(encoding="utf-8"))
        review = None if args.semantic_review is None else json.loads(args.semantic_review.read_text(encoding="utf-8"))
        result = assess_repair(prospective_spec(value, args.run_id.startswith("q")), response,
            run_id=args.run_id, input_sha256=packet_hash(value), semantic_review=review)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(json.dumps({"run_id": args.run_id, "behavioral_acceptance": "not_established",
                          "error": "invalid_retained_input_or_response", "error_type": type(exc).__name__,
                          "learner_benefit": "not_measured"}))
        return 2
    print(json.dumps({"run_id": args.run_id, "packet_sha256": packet_hash(value), **result}, indent=2))
    return result["behavioral_acceptance"] != "bounded_pass"


if __name__ == "__main__":
    raise SystemExit(main())
