from jobhunt.dashboard.waitlist import WaitlistStore


def test_join_upsert_and_counts(tmp_path):
    store = WaitlistStore(db_path=tmp_path / "w.db")
    store.join("a@x.com", "lifetime_99", "2026-07-01")
    store.join("b@x.com", "monthly_19", "2026-07-01")
    store.join("a@x.com", "monthly_29", "2026-07-02")  # resubmit updates pref

    c = store.counts()
    assert c["total"] == 2
    assert c["by_price_pref"]["monthly_29"] == 1
    assert c["by_price_pref"]["lifetime_99"] == 0
