from jobhunt.models import JobPosting


def test_fingerprint_is_stable_and_dedupes_across_sources():
    a = JobPosting(
        job_id="x1", source="greenhouse", source_id="1",
        url="https://example.com/1", title="Senior Backend Engineer",
        company="Acme Robotics", location="Remote (US)", jd_text="..",
    )
    b = JobPosting(
        job_id="x2", source="linkedin", source_id="2",
        url="https://linkedin.com/2", title="Senior Backend Engineer",
        company="Acme Robotics", location="Remote (US)", jd_text="..",
    )
    assert a.fingerprint == b.fingerprint
    assert a.job_id != b.job_id  # different identity, same dedupe key


def test_fingerprint_is_case_insensitive_on_company():
    a = JobPosting(
        job_id="x", source="s", source_id="1", url="u", title="Eng",
        company="ACME", location="NY", jd_text="..",
    )
    b = JobPosting(
        job_id="y", source="s", source_id="2", url="u", title="Eng",
        company="acme", location="NY", jd_text="..",
    )
    assert a.fingerprint == b.fingerprint
