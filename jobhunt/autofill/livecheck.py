"""Dry-run a real posting's hosted form with an obviously fake applicant.

    python -m jobhunt.autofill.livecheck <posting-url> [--shot out.png] [--headed]

Always ``dry_run=True`` and ``submit=False``: every non-GET request is aborted
by the network guard and the submit button is never clicked. Prints the
``browser_apply`` result (including ``blocked_requests``).
"""

from __future__ import annotations

import argparse
import json

FAKE_PLAN: dict = {
    "applicant": {
        "name": "Test Candidate",
        "email": "test@example.com",
        "phone": "+91 98765 43210",
        "location": "Bengaluru, Karnataka, India",
    },
    "cover_letter_text": "This is an automated dry-run test. Please ignore.",
    "answers": {
        "work_authorization": "Yes",
        "requires_sponsorship": "No",
        "years_experience": "3",
        "notice_period": "30 days",
        "current_ctc": "1000000",
        "expected_ctc": "1500000",
        "salary_expectation": "1500000",
        "willing_to_relocate": "Yes",
        "how_did_you_hear": "LinkedIn",
    },
    "links": {
        "linkedin": "https://www.linkedin.com/in/test-candidate-example",
        "github": "https://github.com/test-candidate-example",
        "website": "https://example.com",
    },
}


def dummy_pdf(text: str = "Test Candidate - dummy resume for a dry run") -> bytes:
    """A tiny valid one-page PDF (no dependencies)."""
    stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def main(argv: list[str] | None = None) -> int:
    from jobhunt.autofill.apply import browser_apply

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--shot", help="save a full-page screenshot of the filled form")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    args = ap.parse_args(argv)
    plan = dict(FAKE_PLAN, url=args.url, resume_pdf=dummy_pdf())
    result = browser_apply(plan, submit=False, headless=not args.headed, keep_open=False,
                           dry_run=True, screenshot_path=args.shot)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
