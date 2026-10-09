"""Retrieval parity harness (Phase 0.2 of docs/enterprise-migration.md).

Compares hybrid-retrieval output across storage backends that implement the
VectorStore interface. The point is to prove that swapping backends does not
change what a claims handler sees in the chat/source UI.

Today only the SQLite backend exists, so the default invocation runs
SQLite-vs-SQLite on two independently-built databases as a SELF-CHECK: the
harness must report parity of exactly 1.0, proving the measurement itself is
sound before any second backend exists. Phase 1 adds PostgresVectorStore and
this same runner is pointed at it with:

    .venv/bin/python eval/parity_runner.py --backend-b postgres --tolerance 0.9

(pgvector HNSW is approximate vs SQLite's brute-force matrix search, so the
tolerance is deliberately looser there.)

Deliberately torch-free: a deterministic hash-based fake embedder stands in
for EmbeddingEngine, so parity of RANKING is measured, not embedding quality
(embedding quality is the eval harness's job: eval/run_eval.py).

Usage:
    .venv/bin/python eval/parity_runner.py                      # sqlite vs sqlite self-check
    .venv/bin/python eval/parity_runner.py --top-k 8            # deeper top-k
    .venv/bin/python eval/parity_runner.py --backend-b postgres --tolerance 0.9
"""
import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from backend.rag_engine import SQLiteVectorStore  # noqa: E402
from golden_queries import GOLDEN_QUERIES  # noqa: E402


class _FakeEmbedder:
    """Deterministic 384-dim embeddings from a seeded hash of the text, so
    identical corpora embed identically and parity compares pure ranking."""

    def _embed(self, text):
        rng = np.random.default_rng(int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big"))
        vec = rng.standard_normal(384).astype(np.float32)
        return vec / np.linalg.norm(vec)

    def embed_chunks(self, chunks):
        return [self._embed(c) for c in chunks]

    def embed_query(self, query):
        return self._embed(query)


# ---------------------------------------------------------------------------
# Synthetic corpus -- a compact, domain-flavored stand-in for the seeded
# guidelines + claim dossiers. Deterministic; identical for every backend.
# ---------------------------------------------------------------------------

def _build_corpus():
    docs = [
        # Global policy/guideline documents
        ("regional_labor_rates.xlsx", "xlsx", 1000, "Regional labor rate schedule 2026. Nevada mechanical labor cap is 110 dollars per hour. California mechanical cap is 120 dollars per hour. Sheet metal repair is capped at 62 dollars per hour statewide in Nevada."),
        ("mechanical_labor_sop.pdf", "pdf", 900, "Standard operating procedure for mechanical repair labor. Flat-rate hours follow the industry labor time guides. Authorized operations include catalytic converter and infotainment console replacement."),
        ("glass_endorsement.docx", "docx", 700, "Zero-deductible glass endorsement. Windshield replacement with OEM spec glass waives the comprehensive deductible entirely for safety glass."),
        ("custom_equipment_rider.docx", "docx", 800, "Custom equipment rider. Aftermarket stereos, amplifiers, wheels, and screens not factory-installed are capped at 3500 dollars per occurrence, subject to 10 percent annual depreciation and proof of purchase."),
        ("oem_parts_guarantee.pdf", "pdf", 600, "OEM parts guarantee rider. Mandates brand-new factory-original OEM parts for collision replacements on vehicles under 5 years of age purchased at policy inception."),
        ("fraud_red_flags_sop.pdf", "pdf", 1100, "SIU red flags. Oxidized rust on scratches proves pre-existing damage at least 6 months old. Report claims within 24 hours. Telematics deceleration footprints must match claimant statements."),
        ("hydro_lock_case_study.pdf", "pdf", 950, "Case study: engine hydro-lock. Damages resulting from attempts to restart a stalled engine in standing water are driver-induced consequential damages and are excluded."),
        ("dui_exclusion.txt", "txt", 400, "DUI exclusion directive. Claims arising while the insured was driving under the influence are excluded. Do not assert this exclusion without a supporting police report or citation."),
        ("diminished_value_sop.pdf", "pdf", 850, "Diminished value formula. Base loss value times a 10 percent cap, multiplied by a mileage multiplier from the state schedule."),
        ("state_minimum_limits.xlsx", "xlsx", 500, "State minimum liability limits. California 15/30/5, Texas 30/60/25, New York 25/50/10."),
        ("subrogation_guide.docx", "docx", 750, "Subrogation eligibility. Third-party at-fault collisions allow recovery of the deductible. Recovery unit pursues the at-fault carrier."),
        ("adas_recalibration_guide.docx", "docx", 650, "ADAS recalibration mandate. Rear bumper replacement and structural alignment require electronic recalibration of blind-spot and backup camera sensors, fee 250 to 450 dollars."),
        ("paint_rate_schedule.xlsx", "xlsx", 550, "Paint and refinishing rates. Northern California paint cap 80 dollars per hour. Southern California sheet metal cap 75 dollars per hour."),
        ("rental_upgrade_endorsement.docx", "docx", 450, "Premium rental upgrade endorsement. Provides a full-size rental vehicle while the covered auto is in the shop for an approved repair."),
        ("tow_reimbursement_rider.pdf", "pdf", 480, "Premium towing plus rider. Reimburses towing up to 150 dollars per occurrence and provides 24-hour roadside assistance."),
        ("gap_coverage_endorsement.docx", "docx", 520, "Gap insurance coverage. Waives the difference between the actual cash value and the remaining loan balance when the vehicle is totaled."),
        ("oem_vs_aftermarket_index.xlsx", "xlsx", 600, "OEM versus aftermarket price index. Aftermarket windshields for 2024 Ford F-150 backordered 6 weeks. OEM glass available immediately at 1200 dollars."),
        ("frame_repair_guide.pdf", "pdf", 700, "Frame repair guidelines. Rear body panel pulling requires a frame machine and is billed at the frame rate. 5.0 hours is standard for tailgate alignment."),
        ("adjuster_guide_rear_impact.docx", "docx", 640, "Adjuster guide: rear impact. Inspect bumper reinforcement beam, motor shield, and ADAS sensors after rear-end collisions."),
        ("n20_engine_swap_guide.pdf", "pdf", 800, "BMW N20 engine swap labor times. Long block replacement standard labor is 20.0 hours. Used engine market rate 5200 dollars, new factory block 9500 dollars."),
        ("windshield_replacement_policy.pdf", "pdf", 560, "Windshield replacement policy. Safety glass replacement is mandatory under standard safety rules when cracked or starred."),
        ("theft_recovery_sop.docx", "docx", 620, "Theft recovery SOP. Catalog recovered vehicle condition. Note missing items against the police report scene inventory."),
        ("california_policy_contract.docx", "docx", 1300, "Auto policy contract California. Collision deductible 500 dollars. Comprehensive deductible 500 dollars. Subrogation rights reserved."),
        ("telematic_privacy_notice.pdf", "pdf", 380, "Telematics privacy notice. Driving data is used for claim verification only and is retained per the state records schedule."),
        # Claim-scoped dossiers (matched to the golden claim ids)
        ("police_report_Sterling.pdf", "pdf", 720, "Police report claim 2026-99382. Tesla Model Y stopped at red light was rear-ended at 18 mph by a failing-to-stop vehicle. Stationary 8.4 seconds prior to impact, brake pressure 100 percent.", "#2026-99382"),
        ("shop_email_Sterling.pdf", "pdf", 580, "Larkspur Collision supplement for claim 2026-99382. Rear motor shield cracked, requires replacement. Rear body panel pull 5.0 hours for tailgate alignment. ADAS recalibration required.", "#2026-99382"),
        ("hail_damage_log_Jenkins.pdf", "pdf", 660, "Hail damage photo log claim 2026-10492. 18 hood dents, 24 roof dents, paint unbroken. Windshield cracked radially 4 inches. PDR applicable for unbroken paint.", "#2026-10492"),
        ("oem_glass_quote_Jenkins.xlsx", "xlsx", 430, "OEM windshield quote claim 2026-10492. OEM spec glass 1200 dollars. Aftermarket backordered 6 weeks. Zero-deductible glass endorsement active.", "#2026-10492"),
        ("custom_equipment_receipts_Chen.xlsx", "xlsx", 510, "Receipts claim 2026-30291. Enkei wheels 2400 dollars and Alpine infotainment console 3500 dollars, purchased 2025-08-14 from Elite Custom Auto Sound, total invoice 5900 dollars.", "#2026-30291"),
        ("police_theft_report_Chen.pdf", "pdf", 690, "Theft report claim 2026-30291. Passenger window smashed, infotainment console pried out, catalytic converter sawed off. Reported same morning.", "#2026-30291"),
        ("telematics_log_Rostova.pdf", "pdf", 740, "Telematics claim 2026-55912. 45 mph in standing water, RPM dropped, stalled with hydraulic lock flag, 3 ignition restart attempts all failed.", "#2026-55912"),
        ("engine_diagnostic_Rostova.pdf", "pdf", 700, "Engine diagnostic claim 2026-55912. Standing water in intake and charge pipe, water ejected from cylinders 2 and 3, bent connecting rod, hairline block fracture at starter mount.", "#2026-55912"),
    ]
    return docs


# ---------------------------------------------------------------------------
# Query set -- the golden eval queries plus deterministic synthetic ones.
# ---------------------------------------------------------------------------

def _build_queries(include_golden=True):
    queries = []
    if include_golden:
        for q in GOLDEN_QUERIES:
            queries.append((q["query"], q.get("claim_id")))
    synthetic = [
        ("What is the Nevada mechanical labor rate cap?", None),
        ("Does the zero-deductible glass endorsement waive the comprehensive deductible?", None),
        ("Custom equipment cap per occurrence", None),
        ("OEM parts required under 5 years?", None),
        ("rules for diminished value calculation", None),
        ("state minimum liability limits California", None),
        ("subrogation deductible recovery third party fault", None),
        ("ADAS recalibration rear bumper replacement", None),
        ("hydro lock restart attempts excluded", None),
        ("DUI exclusion requires police report", None),
        ("windshield backorder aftermarket f150", None),
        ("N20 engine swap standard labor hours", None),
        ("claim 2026-99382 rear impact motor shield", "#2026-99382"),
        ("claim 2026-10492 hail dents PDR", "#2026-10492"),
        ("claim 2026-30291 custom equipment total", "#2026-30291"),
        ("claim 2026-55912 starter attempts hydro lock", "#2026-55912"),
        ("glass deductible endorsement zero", None),
        ("theft recovery scene inventory missing items", None),
        ("rental upgrade full size vehicle", None),
        ("paint cap northern california", None),
    ]
    queries.extend(synthetic)
    return queries


# ---------------------------------------------------------------------------
# Backend registry -- Phase 1 registers "postgres" here.
# ---------------------------------------------------------------------------

def _sqlite_factory(workdir):
    return SQLiteVectorStore(
        db_path=os.path.join(workdir, "store.db"),
        storage_dir=os.path.join(workdir, "docs"),
    )


def _postgres_factory(workdir):
    """Phase 1: PostgresVectorStore against the env-configured POSTGRES_DSN.

    Both backends ingest the same corpus in one run, so each side gets its own
    tenant label (derived from the temp workdir basename) to avoid one side
    overwriting the other in the shared database."""
    from backend.postgres_store import PostgresVectorStore

    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        raise SystemExit("POSTGRES_DSN is required for the 'postgres' backend")
    tenant = "parity-" + os.path.basename(workdir)
    return PostgresVectorStore(
        dsn=dsn,
        tenant_id=tenant,
        storage_dir=os.path.join(workdir, "docs"),
    )


BACKENDS = {
    "sqlite": _sqlite_factory,
    "postgres": _postgres_factory,
}


def _item_key(match):
    """Parent ids differ across databases; (filename, content) is the stable
    identity of a retrieved passage."""
    return (match["filename"], match["content"])


def _parity(backend_a, backend_b, corpus, queries, embedder, top_k, tolerance):
    """Ingests the corpus into both backends, runs every query, and reports
    how much of backend A's top-k ranking backend B reproduces."""
    doc_a, doc_b = 0, 0
    for filename, file_type, file_size, text, *rest in corpus:
        claim_id = rest[0] if rest else None
        backend_a.add_document(filename, file_type, file_size, text, embedder, claim_id=claim_id)
        backend_b.add_document(filename, file_type, file_size, text, embedder, claim_id=claim_id)
        doc_a += 1
        doc_b += 1

    print(f"Corpus: {doc_a} documents indexed into each backend ({len(queries)} queries, top_k={top_k})")
    print(f"Parity tolerance: mean recall@{top_k} >= {tolerance}\n")

    recalls, exacts = [], []
    worst = (None, 1.0)
    for query, claim_id in queries:
        emb = embedder.embed_query(query)
        res_a = backend_a.search_similarity(emb, query, claim_id=claim_id, top_k=top_k)
        res_b = backend_b.search_similarity(emb, query, claim_id=claim_id, top_k=top_k)

        key_a = [_item_key(m) for m in res_a]
        key_b = [_item_key(m) for m in res_b]
        set_b = set(key_b)

        if not key_a:
            recall = 1.0 if not key_b else 0.0
        else:
            recall = len([k for k in key_a if k in set_b]) / len(key_a)
        exact = sum(1 for ka, kb in zip(key_a, key_b, strict=False) if ka == kb) / top_k

        recalls.append(recall)
        exacts.append(exact)
        if recall < worst[1]:
            worst = (query, recall)

        flag = "OK " if recall >= tolerance else "FAIL"
        print(f"[{flag}] recall@{top_k}={recall:.2f} exact-match={exact:.2f}  query: {query[:70]}")

    mean_recall = float(np.mean(recalls))
    mean_exact = float(np.mean(exacts))
    min_recall = float(np.min(recalls))
    print(f"\nMean recall@{top_k}: {mean_recall:.4f}  (min {min_recall:.4f} on: {worst[0]!r})")
    print(f"Mean exact-match@{top_k}: {mean_exact:.4f}")

    ok = mean_recall >= tolerance
    print(f"\nPARITY {'PASS' if ok else 'FAIL'}: mean recall@{top_k} {mean_recall:.4f} vs tolerance {tolerance}")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Retrieval parity harness (Phase 0.2).")
    parser.add_argument("--backend-a", default="sqlite", help="backend under test (default sqlite)")
    parser.add_argument("--backend-b", default="sqlite", help="reference backend (default sqlite = self-check)")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--tolerance", type=float, default=1.0,
                        help="minimum mean recall@k (use 0.9 once a pgvector backend exists)")
    parser.add_argument("--no-golden", action="store_true", help="skip the golden eval queries")
    args = parser.parse_args()

    for name in (args.backend_a, args.backend_b):
        if name not in BACKENDS:
            sys.exit(f"Backend {name!r} is not registered. Registered: {sorted(BACKENDS)}")

    embedder = _FakeEmbedder()
    corpus = _build_corpus()
    queries = _build_queries(include_golden=not args.no_golden)

    with tempfile.TemporaryDirectory() as tmp:
        workdir_a = os.path.join(tmp, "a")
        workdir_b = os.path.join(tmp, "b")
        os.makedirs(workdir_a)
        os.makedirs(workdir_b)
        backend_a = BACKENDS[args.backend_a](workdir_a)
        backend_b = BACKENDS[args.backend_b](workdir_b)
        try:
            ok = _parity(backend_a, backend_b, corpus, queries, embedder, args.top_k, args.tolerance)
        finally:
            # Postgres leaves the parity tenants behind in the shared database;
            # drop them so a rerun is deterministic (and CI DBs stay clean).
            _cleanup_postgres_tenants(["a", "b"])

    sys.exit(0 if ok else 1)


def _cleanup_postgres_tenants(workdirs):
    """Removes the parity-* tenants a postgres run creates, so re-running on the
    same database is idempotent. No-op when no Postgres is configured."""
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        return
    try:
        import psycopg
    except ImportError:
        return
    with psycopg.connect(dsn) as conn:
        for base in workdirs:
            conn.execute(
                "DELETE FROM documents WHERE tenant_id = %s",
                ("parity-" + base,),
            )
        conn.commit()


if __name__ == "__main__":
    main()
