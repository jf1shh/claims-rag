import json
import time
from typing import Optional, List, Dict, Any

from backend.llm_client import ChatClientError

CLAIMS_DATA = [
    {
        "id": "#2026-99382",
        "status": "Under Review",
        "insured": "Matthew Sterling",
        "vehicle": "2023 Tesla Model Y",
        "facility": "Caliber Collision (Los Angeles, CA)",
        "totalEst": "$4,850",
        "plan": "Plan B (Premium)",
        "deductible": "$500 Collision Deductible",
        "endorsements": ["OEM Parts Guarantee", "Premium Rental Upgrade"],
        "estimate": [
            { "cat": "Body", "op": "Replace Rear Bumper Cover", "rate": "$75/hr", "qty": "6.0 hrs", "total": "$450" },
            { "cat": "Paint", "op": "Refinish Bumper & Blend Trunk", "rate": "$75/hr", "qty": "8.0 hrs", "total": "$600" },
            { "cat": "Safety", "op": "ADAS Backup Sensor Calibration", "rate": "Flat", "qty": "1 Unit", "total": "$450" },
            { "cat": "Frame", "op": "Pull Rear Body Panel (Alignment)", "rate": "$85/hr", "qty": "5.0 hrs", "total": "$425" },
            { "cat": "Mechanical", "op": "Replace Rear Motor Shield & Alignment", "rate": "$120/hr", "qty": "4.0 hrs", "total": "$480" }
        ]
    },
    {
        "id": "#2026-10492",
        "status": "Open",
        "insured": "Sarah Jenkins",
        "vehicle": "2024 Ford F-150 SuperCrew",
        "facility": "Apex Auto Body (San Francisco, CA)",
        "totalEst": "$6,800",
        "plan": "Plan A (Standard)",
        "deductible": "$500 Comprehensive Deductible",
        "endorsements": ["Zero-Deductible Glass", "Premium Towing Plus"],
        "estimate": [
            { "cat": "PDR", "op": "Paintless Dent Repair (42 dents)", "rate": "Flat", "qty": "1 Event", "total": "$3,200" },
            { "cat": "Paint", "op": "Refinish Passenger Doors", "rate": "$75/hr", "qty": "12.0 hrs", "total": "$900" },
            { "cat": "Glass", "op": "Replace Windshield (OEM Spec Glass)", "rate": "Flat", "qty": "1 Unit", "total": "$1,200" },
            { "cat": "Safety", "op": "Windshield ADAS Camera Recalibration", "rate": "Flat", "qty": "1 Unit", "total": "$350" }
        ]
    },
    {
        "id": "#2026-30291",
        "status": "Under Investigation",
        "insured": "David Chen",
        "vehicle": "2022 Honda Civic Sport",
        "facility": "Elite Fleet Repair (Las Vegas, NV)",
        "totalEst": "$9,400",
        "plan": "Plan B (Premium)",
        "deductible": "$250 Comprehensive Deductible",
        "endorsements": ["OEM Parts Guarantee", "Custom Equipment ($3.5k limit)"],
        "estimate": [
            { "cat": "Mechanical", "op": "Replace Cut Catalytic Converter (OEM)", "rate": "$120/hr", "qty": "2.0 hrs", "total": "$1,500" },
            { "cat": "Body", "op": "Replace 4x Sport Wheels & Tires", "rate": "Flat", "qty": "4 Units", "total": "$2,400" },
            { "cat": "Electrical", "op": "Replace Stolen Infotainment Console", "rate": "$120/hr", "qty": "6.0 hrs", "total": "$3,500" },
            { "cat": "Body", "op": "Repair Passenger Side Key Scratches", "rate": "$75/hr", "qty": "8.0 hrs", "total": "$600" }
        ]
    },
    {
        "id": "#2026-55912",
        "status": "SIU Flagged",
        "insured": "Elena Rostova",
        "vehicle": "2020 BMW 330i xDrive",
        "facility": "Classic Auto Restoration (Orlando, FL)",
        "totalEst": "$12,500",
        "plan": "Plan A (Standard)",
        "deductible": "$500 Comprehensive Deductible",
        "endorsements": ["Gap Insurance Coverage"],
        "estimate": [
            { "cat": "Mechanical", "op": "Replace Engine Block (Hydro-locked)", "rate": "$110/hr", "qty": "20.0 hrs", "total": "$9,500" },
            { "cat": "Mechanical", "op": "Flush Oil Lines and Cooling System", "rate": "$110/hr", "qty": "4.0 hrs", "total": "$440" },
            { "cat": "Electrical", "op": "Replace Submerged ECU & Sensors", "rate": "Flat", "qty": "1 Unit", "total": "$2,000" }
        ]
    }
]

class AgenticRAGRouter:

    def _get_claim_context_markdown(self, claim_id: str) -> str:
        claim = next((c for c in CLAIMS_DATA if c["id"] == claim_id), None)
        if not claim:
            return ""

        md = []
        md.append(f"### ACTIVE CLAIM SUMMARY DOSSIER ({claim_id})")
        md.append(f"- **Insured Claimant**: {claim['insured']}")
        md.append(f"- **Vehicle Description**: {claim['vehicle']}")
        md.append(f"- **Repair Facility**: {claim['facility']}")
        md.append(f"- **Total Shop Estimate**: {claim['totalEst']}")
        md.append(f"- **Policy Plan Type**: {claim['plan']}")
        md.append(f"- **Deductible**: {claim['deductible']}")
        md.append(f"- **Active Endorsements/Riders**: {', '.join(claim['endorsements'])}")
        md.append("")
        md.append("#### Repair Shop Estimate Line Items:")
        md.append("| Category | Repair Operation Description | Hourly Rate/Flat Fee | Quantity | Subtotal |")
        md.append("| --- | --- | --- | --- | --- |")
        for row in claim["estimate"]:
            md.append(f"| {row['cat']} | {row['op']} | {row['rate']} | {row['qty']} | {row['total']} |")

        return "\n".join(md)

    def run_query(
        self,
        query_text: str,
        claim_id: Optional[str],
        engine: str,
        embedding_engine: Any,
        vector_store: Any,
        reranking_engine: Any = None,
        llm_client: Any = None,
    ) -> Dict[str, Any]:
        """Runs the query through a stateful, self-correcting agentic planning & retrieval loop."""
        logs = []
        start_time = time.time()

        logs.append(f"🧠 [Agentic Coordinator] Initializing planner for query: '{query_text}'")
        if claim_id:
            logs.append(f"🔍 [Agentic Coordinator] Active claim folder scope: {claim_id}")

        # If simulated mode, execute high-fidelity structured routing
        if engine == "simulated":
            return self._run_simulated_agent(query_text, claim_id, vector_store, embedding_engine, reranking_engine, logs, start_time)
        if llm_client is None:
            return self._run_simulated_agent(query_text, claim_id, vector_store, embedding_engine, reranking_engine, logs, start_time)
        # Only "lm-studio" is accepted for the online path. engine used to be
        # used directly as a request URL when it wasn't "lm-studio" -- an SSRF
        # vector, since the retrieved claim/policy context would be POSTed to
        # whatever URL a caller supplied. There's exactly one supported local
        # engine, so anything else is rejected rather than treated as a target.
        if engine != "lm-studio":
            logs.append(f"❌ [Config Error] Unknown engine '{engine}'. Only 'simulated' and 'lm-studio' are supported.")
            return {
                "answer": f"Unknown engine '{engine}'. Please select 'simulated' or 'lm-studio'.",
                "sources": [],
                "claim_dossier": None,
                "engine": engine,
                "pipeline_logs": logs
            }

        return self._run_online_agent(query_text, claim_id, vector_store, embedding_engine, reranking_engine, logs, start_time, llm_client)

    def _run_online_agent(
        self,
        query_text: str,
        claim_id: Optional[str],
        vector_store: Any,
        embedding_engine: Any,
        reranking_engine: Any,
        logs: List[str],
        start_time: float,
        llm_client: Any
    ) -> Dict[str, Any]:
        # Step 1: Query Decomposition (Planner Call)
        logs.append("📋 [Step 1: Planning] Decomposing query into target sub-queries...")
        model_name = llm_client.models()[0]
        plan = self._get_llm_plan(query_text, claim_id, llm_client, model_name)

        logs.append(f"📄 [Agent Plan] Route Guidelines: {plan['needs_global_policies']} | Route Claim Dossier: {plan['needs_claim_dossier']}")
        for idx, sub_q in enumerate(plan["sub_queries"]):
            logs.append(f"   ➔ Sub-query {idx+1}: '{sub_q}'")

        # Step 2: Tool Execution (Retrieve context)
        all_matches = []
        seen_passages = set()

        for sub_q in plan["sub_queries"]:
            sub_start = time.time()
            query_emb = embedding_engine.embed_query(sub_q)

            # Retrieve policies
            if plan["needs_global_policies"]:
                matches = vector_store.search_similarity(
                    query_emb,
                    sub_q,
                    claim_id=None,
                    reranking_engine=reranking_engine,
                    top_k=3
                )
                for m in matches:
                    p_key = (m["filename"], m["content"][:50])
                    if p_key not in seen_passages:
                        seen_passages.add(p_key)
                        all_matches.append(m)

            logs.append(f"⚙️ [Tool Exec] Retrieval for '{sub_q}' complete in {(time.time() - sub_start)*1000:.1f}ms")

        # Claim dossier: always pull every chunk from the active claim's own
        # documents directly, not via semantic search, and not gated on the
        # planner's needs_claim_dossier flag (found unreliable -- see the
        # zero-context bug fixed above). Claim dossiers are small (a handful
        # of documents, 1-2 chunks each) and always relevant once a claim is
        # active, so they shouldn't have to out-rank a globally-similar policy
        # document for one of the semantic-search slots. That was silently
        # dropping the one claim-specific fact (a receipt total, an inspection
        # detail) that actually answers a claim-scoped multi-hop question --
        # e.g. "does this claim's total exceed the endorsement cap" needs both
        # the endorsement doc *and* the claim's own receipt in the same
        # context, and single-shot semantic search wasn't guaranteeing that.
        claim_chunks = []
        if claim_id:
            claim_chunks = vector_store.get_claim_chunks(claim_id)
            if claim_chunks:
                logs.append(f"📁 [Tool Exec] Loaded {len(claim_chunks)} chunk(s) from claim {claim_id}'s own documents (guaranteed, unranked).")

        # Step 3: Self-Correction / Query Translation Loop
        #
        # Only needed when there's truly nothing to ground on. If the claim's
        # own documents were found (claim_chunks), that alone is sufficient
        # grounding even with zero global policy matches -- no need to waste a
        # fallback search. Must still trigger for global-only queries where
        # the planner's sub-queries came up empty -- see the zero-context
        # hallucination bug this was built to fix.
        if not all_matches and not claim_chunks:
            if claim_id:
                logs.append("⚠️ [Self-Correction] Zero search matches returned. Executing Query Translation fallback...")
                fallback_q = f"claim details {claim_id}"
                logs.append(f"   ➔ Rewriting search target to: '{fallback_q}'")

                fallback_emb = embedding_engine.embed_query(fallback_q)
                fallback_matches = vector_store.search_similarity(
                    fallback_emb,
                    fallback_q,
                    claim_id=claim_id,
                    reranking_engine=reranking_engine,
                    top_k=4
                )
                all_matches.extend(fallback_matches)
                logs.append(f"🔄 [Self-Correction] Recovered {len(fallback_matches)} dossier sources.")
            else:
                logs.append("⚠️ [Self-Correction] Zero search matches returned. Retrying against global policies with the original query...")

                fallback_emb = embedding_engine.embed_query(query_text)
                fallback_matches = vector_store.search_similarity(
                    fallback_emb,
                    query_text,
                    claim_id=None,
                    reranking_engine=reranking_engine,
                    top_k=4
                )
                all_matches.extend(fallback_matches)
                logs.append(f"🔄 [Self-Correction] Recovered {len(fallback_matches)} global policy sources.")

        # Sort global matches by score and cap at 4, then prepend the claim's
        # own (guaranteed, unranked) chunks -- they never compete for that cap.
        all_matches.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        top_matches = claim_chunks + all_matches[:4]

        # Hard stop: never let the LLM synthesize freely with zero retrieved
        # context. Without this, an ungrounded call reliably fabricates both
        # an answer and citations to filenames that don't exist in the corpus.
        if not top_matches:
            logs.append("❌ [Synthesis Skipped] No supporting documents found after self-correction; refusing to answer ungrounded.")
            elapsed = (time.time() - start_time) * 1000
            logs.append(f"✅ [Agentic Coordinator] Completed reasoning cycle in {elapsed:.1f}ms")
            return {
                "answer": "I couldn't find any supporting documents for this question in the available guidelines"
                           + (f" or claim {claim_id} dossier" if claim_id else "") + ". "
                           "Please rephrase the question or confirm the relevant policy/claim documents have been uploaded.",
                "sources": [],
                "claim_dossier": None,
                "engine": "lm-studio (agentic)",
                "pipeline_logs": logs
            }

        # Step 4: Final LLM Synthesis
        logs.append("✍️ [Step 2: Synthesis] Invoking local LLM to generate context-grounded audit response...")

        context_blocks = []
        for idx, match in enumerate(top_matches):
            context_blocks.append(
                f"--- SOURCE {idx+1} | File: {match['filename']} (Sim: {match['score']:.3f}) ---\n{match['content']}\n"
            )
        context_text = "\n".join(context_blocks)

        system_prompt = (
            "You are an expert AI claims handler assistant. Your job is to answer the user's questions about insurance claims, "
            "policies, or guidelines using ONLY the provided reference sources and the active claim summary dossier. "
            "When a source lists multiple line items (e.g. a receipt, an itemized estimate), enumerate every item and its value "
            "individually before computing any total, sum, or cap comparison -- do not calculate from a single item if more than "
            "one applies. Perform calculations (payouts, caps, deductibles) if asked. "
            "If the source guidelines exclude coverage or indicate fraud, state it clearly. Cite source filenames in your explanation."
        )

        # Build claim context markdown
        claim_context = ""
        if claim_id:
            claim_context = self._get_claim_context_markdown(claim_id)

        user_prompt = (
            f"Active Claim ID: {claim_id if claim_id else 'None (Global Scope)'}\n\n"
            f"{claim_context}\n\n"
            f"Here are the matching reference sources from the policy guidelines:\n{context_text}\n"
            f"User Question: {query_text}\n\n"
            "Generate your structured response:"
        )

        try:
            # Timeouts are owned by the injected client: the configured
            # synthesis timeout (default 120s for a 14B-class model on consumer
            # hardware) applies unless this model is the fast planner.
            answer = llm_client.complete(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_prompt}],
                model=llm_client.model_for_stage("synthesis"),
                temperature=0.1,
                max_tokens=1000,
            )
        except ChatClientError as e:
            logs.append(f"❌ [Synthesis Error] LLM generation failed: {e}. Falling back to simulation.")
            return self._run_simulated_agent(query_text, claim_id, vector_store, embedding_engine, reranking_engine, logs, start_time)

        elapsed = (time.time() - start_time) * 1000
        logs.append(f"✅ [Agentic Coordinator] Completed reasoning cycle in {elapsed:.1f}ms")

        return {
            "answer": answer,
            "sources": [
                {
                    "filename": m["filename"],
                    "file_type": m["file_type"],
                    "content": m["content"],
                    "score": round(m["score"], 3)
                }
                for m in top_matches
            ],
            # The LLM is also grounded in this claim summary dossier (injected
            # directly into the prompt, not retrieved via search), so callers
            # scoring groundedness against only `sources` will see a partial
            # picture for claim-scoped answers. Surfaced separately rather than
            # folded into `sources` since it isn't a search result.
            "claim_dossier": claim_context if claim_context else None,
            "engine": "lm-studio (agentic)",
            "pipeline_logs": logs
        }

    def _get_llm_plan(self, query_text: str, claim_id: Optional[str], llm_client: Any, model_name: str) -> Dict[str, Any]:
        """Requests a structured JSON plan from the LLM via the injected client."""
        system_prompt = (
            "You are an AI Claims Planner. Decompose the claims query into target document searches.\n"
            "Determine if the query needs: \n"
            "1. global policies (guidelines, schedules, labor limits)\n"
            "2. claim dossier files (police report, telematics, repair invoice)\n"
            "Write 1 or 2 targeted search queries.\n"
            "Respond ONLY with a JSON object in this format:\n"
            '{"needs_global_policies": true, "needs_claim_dossier": true, "sub_queries": ["query 1", "query 2"]}'
        )

        user_prompt = f"Claim ID: {claim_id}\nClaims Query: {query_text}"

        fallback_plan = {
            "needs_global_policies": True,
            "needs_claim_dossier": True if claim_id else False,
            "sub_queries": [query_text]
        }

        try:
            text = llm_client.complete(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_prompt}],
                model=llm_client.model_for_stage("planning"),
                temperature=0.0,
                max_tokens=150,
            ).strip()
            # Parse JSON out of response
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            parsed = json.loads(text)
            # The plan comes from a small local model -- valid JSON with
            # missing/mistyped keys is common, and callers index the plan
            # dict directly, so every field is coerced to the expected
            # shape here (falling back per-field) rather than trusting it.
            if isinstance(parsed, dict):
                sub_queries = parsed.get("sub_queries")
                if not (isinstance(sub_queries, list)
                        and sub_queries
                        and all(isinstance(q, str) and q.strip() for q in sub_queries)):
                    sub_queries = [query_text]
                return {
                    "needs_global_policies": bool(parsed.get("needs_global_policies", True)),
                    "needs_claim_dossier": bool(parsed.get("needs_claim_dossier", bool(claim_id))),
                    "sub_queries": sub_queries[:3],
                }
        except ChatClientError:
            pass
        except Exception:
            pass

        return fallback_plan

    def _run_simulated_agent(
        self,
        query_text: str,
        claim_id: Optional[str],
        vector_store: Any,
        embedding_engine: Any,
        reranking_engine: Any,
        logs: List[str],
        start_time: float
    ) -> Dict[str, Any]:
        """Performs high-fidelity local RAG queries and evaluates policy limits via Python rule engine."""
        logs.append("⚙️ [Simulated Agent] Initializing local rule calculator...")

        # Run actual search query on local SQLite database to fetch matching pieces
        query_emb = embedding_engine.embed_query(query_text)
        matches = vector_store.search_similarity(
            query_emb,
            query_text,
            claim_id=claim_id,
            reranking_engine=reranking_engine,
            top_k=4
        )

        # Build reasoning logs based on matches and claim_id
        logs.append("📋 [Sub-Task 1] Parsing active case details & estimate rows...")
        logs.append("📋 [Sub-Task 2] Retrieving regional guidelines and deductible endorsements...")

        # Detect audit parameter type
        q_lower = query_text.lower()
        audit_type = "general"
        if "labor" in q_lower or "rate" in q_lower or "cap" in q_lower:
            audit_type = "labor"
        elif "oem" in q_lower or "aftermarket" in q_lower or "lkq" in q_lower:
            audit_type = "oem"
        elif "fraud" in q_lower or "red flag" in q_lower or "siu" in q_lower:
            audit_type = "fraud"
        elif "letter" in q_lower or "settlement" in q_lower or "decision" in q_lower:
            audit_type = "letter"

        logs.append(f"🔍 [Agentic Planner] Detected audit parameter: {audit_type.upper()}")

        answer_parts = []

        if claim_id == "#2026-99382": # Matthew Sterling
            if audit_type == "labor":
                logs.append("⚙️ [Tool Exec] policy_search: Found Regional_Labor_Rates_2026.xlsx and SOP_Auto_Repair_Labor_Rates.pdf")
                logs.append("⚙️ [Tool Exec] dossier_search: Found shop_email_thread_Sterling.pdf (5.0 hours frame time)")
                logs.append("🧮 [Calculator Tool] Checking ADAS recalibration requirement: rear bumper replacement mandates recalibration per Adjuster_Guide_Rear_Impact.docx ($250-$450 fee).")
                logs.append("🧮 [Calculator Tool] Checking California Sheet Metal rate: $75/hr (complies with cap of $75/hr).")
                logs.append("🧮 [Calculator Tool] Checking California Mechanical rate: $120/hr (complies with cap of $120/hr).")
                answer_parts.extend([
                    "### ⚡ Regional Labor Rate & Estimate Audit: Matthew Sterling (#2026-99382)",
                    "**Status: COMPLIANT (estimate approved)**",
                    "",
                    "1. **Labor Rate Auditing**:",
                    "   * **Body & Paint Rates**: The shop charges **$75.00/hr** for Bumper Cover Replacement and Painting. This complies with the **$75.00/hr Southern California Regional Cap** listed in *Regional_Labor_Rates_2026.xlsx*.",
                    "   * **Frame rate**: The shop charges **$85.00/hr** for panel pulling, which matches the maximum allowable California frame rate (PASSED).",
                    "   * **Mechanical rate**: The shop charges **$120.00/hr** for motor shield replacement, matching the California mechanical cap of **$120.00/hr** (PASSED).",
                    "2. **ADAS Recalibration Requirement**:",
                    "   * *Adjuster_Guide_Rear_Impact.docx* mandates electronic ADAS recalibration ($250-$450 fee) on any rear bumper replacement or structural alignment, since blind-spot sensors and backup cameras are housed in the rear bumper assembly.",
                    "   * The estimate includes **$450.00** for ADAS Backup Sensor Calibration — within the mandated fee range (PASSED).",
                    "",
                    "**Summary Recommendation**: Approve the repair labor rates and hours. Proceed to parts evaluation."
                ])
            elif audit_type == "oem":
                logs.append("⚙️ [Tool Exec] policy_search: Found Rider_OEM_Parts_Guarantee.pdf")
                logs.append("⚙️ [Tool Exec] dossier_search: Found vehicle dossier details")
                logs.append("🧮 [Calculator Tool] Checking vehicle age: 2023 Tesla Y in 2026 is 3 years old. Eligible under OEM parts rider terms.")
                answer_parts.extend([
                    "### 🔧 OEM Parts Rider Audit: Matthew Sterling (#2026-99382)",
                    "**Status: APPROVED (OEM components authorized)**",
                    "",
                    "1. **Endorsement Checklist**:",
                    "   * Active Endorsements: **OEM Parts Guarantee** (verified active).",
                    "   * Rider Terms (*Rider_OEM_Parts_Guarantee.pdf*): Mandates the usage of brand-new, factory-original OEM parts for all collision replacements on vehicles **under 5 years of age**, purchased at policy inception.",
                    "2. **Vehicle Age Verification**:",
                    "   * Vehicle: 2023 Tesla Model Y.",
                    "   * Date of Loss Audit: 2026.",
                    "   * Age Calculation: 2026 - 2023 = **3 years old**. The vehicle qualifies under the 5-year OEM mandate threshold.",
                    "3. **Parts Authorization**:",
                    "   * Bumper Cover ($450) and Motor Shield ($480) are approved at factory OEM retail list prices. LKQ or aftermarket alternatives are prohibited.",
                    "",
                    "**Summary Recommendation**: Authorize factory OEM parts for all replacement operations on the estimate."
                ])
            elif audit_type == "fraud":
                logs.append("⚙️ [Tool Exec] policy_search: Found SOP_Claims_Fraud_Red_Flags.pdf")
                logs.append("⚙️ [Tool Exec] dossier_search: Found telematics_log_Sterling.pdf (Rear collision force of 4.2G at 14 mph)")
                logs.append("🔍 [Reflection] Checking sensor timelines against statement: Deceleration footprint matches.")
                answer_parts.extend([
                    "### 🔍 SIU Fraud Red Flags Audit: Matthew Sterling (#2026-99382)",
                    "**Status: CLEAR (no flags detected)**",
                    "",
                    "1. **Incident Telematics Audit**:",
                    "   * Telematics extract (*telematics_log_Sterling.pdf*) reports a rear deceleration impact force of **4.2G at 14 mph** on January 10, 2026, at 14:32:05.",
                    "   * This sensor footprint correlates perfectly with the claimant's statement and police report details of being rear-ended by a third party while stopped at a red light.",
                    "2. **Pre-Existing Damage Check**:",
                    "   * Photo descriptions and diagnostic codes show no overlapping impact wear or pre-existing trunk rust.",
                    "3. **Timeline Check**:",
                    "   * Loss reported within 24 hours of incident (PASSED).",
                    "",
                    "**Summary Recommendation**: No SIU flags active. Approve claim liability as 100% third-party fault."
                ])
            else: # letter / general
                logs.append("⚙️ [Tool Exec] policy_search: Found Auto_Policy_Contract_California.docx")
                logs.append("🧮 [Calculator Tool] Deductible Math: Apply $500 collision deductible; Payout = $4,850 - $500 = $4,350.")
                answer_parts.extend([
                    "### 📝 Claim Decision & Payout Settlement Letter: Matthew Sterling",
                    "**Date**: July 16, 2026",
                    "**Claim ID**: #2026-99382",
                    "**Insured**: Matthew Sterling",
                    "**Vehicle**: 2023 Tesla Model Y",
                    "",
                    "Dear Matthew Sterling,",
                    "",
                    "We have completed the audit of your vehicle damage estimate submitted by Caliber Collision for the rear impact collision on January 10, 2026.",
                    "",
                    "**Settlement Calculations**:",
                    "*   **Total Approved Repairs**: $4,850.00",
                    "    *   *Body & Paint labor*: $1,050.00",
                    "    *   *ADAS Calibration (sensor replacement)*: $450.00",
                    "    *   *Rear Bumper pulling (5.0 hours)*: $425.00",
                    "    *   *OEM Motor Shield replacement & alignment*: $2,925.00",
                    "*   **Collision Deductible Applied**: -$500.00",
                    "*   **Net Settlement Payout**: **$4,350.00**",
                    "",
                    "Under the *Rider_OEM_Parts_Guarantee.pdf*, factory-original OEM parts have been fully approved.",
                    "",
                    "Since you were rear-ended while stationary, our recovery unit is actively pursuing subrogation against State Farm Insurance (the third-party carrier) to recover the repair costs and refund your $500.00 deductible.",
                    "",
                    "Sincerely,",
                    "**Claims Adjuster Copilot**",
                    "*Citations: Auto_Policy_Contract_California.docx, Adjuster_Guide_Rear_Impact.docx, Rider_OEM_Parts_Guarantee.pdf*"
                ])

        elif claim_id == "#2026-10492": # Sarah Jenkins
            if audit_type == "labor":
                logs.append("⚙️ [Tool Exec] policy_search: Found Regional_Labor_Rates_2026.xlsx")
                logs.append("🧮 [Calculator Tool] Checking Apex Auto Body paint rate: $75/hr <= Northern California cap of $80/hr.")
                answer_parts.extend([
                    "### ⚡ Regional Labor Rate & Estimate Audit: Sarah Jenkins (#2026-10492)",
                    "**Status: COMPLIANT (estimate approved)**",
                    "",
                    "1. **Labor Rate Auditing**:",
                    "   * **Paint labor rate**: Apex Auto Body (San Francisco, CA) charges **$75.00/hr** for door painting. This is compliant with the **$80.00/hr Northern California Regional Cap** in *Regional_Labor_Rates_2026.xlsx*.",
                    "2. **Operation Hour Limits**:",
                    "   * The request for **12.0 hours** to refinish passenger doors and blend paint is within the standard doors panel refinishing guidelines (PASSED).",
                    "",
                    "**Summary Recommendation**: Labor rates are compliant. Proceed to parts and operations audit."
                ])
            elif audit_type == "oem":
                logs.append("⚙️ [Tool Exec] policy_search: Found Endorsement_Windshield_Zero_Deductible.docx")
                logs.append("⚙️ [Tool Exec] dossier_search: Found OEM_vs_Aftermarket_Price_Index.xlsx (windshield backorder status)")
                logs.append("🧮 [Calculator Tool] Windshield Replacement: Aftermarket backordered 6 weeks. Approve OEM windshield as emergency fallback.")
                answer_parts.extend([
                    "### 🔧 OEM Parts Rider Audit: Sarah Jenkins (#2026-10492)",
                    "**Status: APPROVED (Standard policy override)**",
                    "",
                    "1. **Endorsement Checklist**:",
                    "   * Active Endorsements: **Zero-Deductible Glass**, **Premium Towing Plus**.",
                    "   * The policy **does not** contain the *Rider_OEM_Parts_Guarantee.pdf*.",
                    "2. **Windshield Parts Selection**:",
                    "   * The shop wrote for an **OEM spec windshield replacement** at **$1,200.00**.",
                    "   * Under standard policy rules, adjusters write for aftermarket glass. However, *OEM_vs_Aftermarket_Price_Index.xlsx* indicates aftermarket glass for the 2024 Ford F-150 is currently backordered for 6 weeks.",
                    "   * To avoid extensive rental car costs, the OEM windshield is authorized as a warehouse emergency fallback.",
                    "",
                    "**Summary Recommendation**: Approve the $1,200.00 OEM windshield under the Zero-Deductible Glass endorsement ($0.00 deductible applied)."
                ])
            elif audit_type == "fraud":
                logs.append("⚙️ [Tool Exec] policy_search: Found Case_Study_Hail_Damage_PlanA.pdf")
                logs.append("⚙️ [Tool Exec] dossier_search: Found dent_photos_description_Jenkins.pdf (18 hood dents, 24 roof dents, paint unbroken)")
                logs.append("🔍 [Reflection] Checking PDR and repainting scope against the case study precedent: distinct damage, not overlapping.")
                answer_parts.extend([
                    "### 🔍 SIU Fraud Red Flags Audit: Sarah Jenkins (#2026-10492)",
                    "**Status: CLEAR (no flags detected; underpayment risk flagged for review)**",
                    "",
                    "1. **Estimate Duplication Check**:",
                    "   * The estimate charges **$3,200.00** for Paintless Dent Repair (PDR, 42 hood/roof dents) and **$2,400.00** for conventional bodywork and repainting of the passenger doors.",
                    "   * *Case_Study_Hail_Damage_PlanA.pdf* (precedent for this exact claim ID) confirms these are two distinct, non-overlapping repairs — PDR for unbroken-paint dents, repainting for the passenger door body damage — totaling **$6,800.00** together with the $1,200.00 windshield replacement. No duplication.",
                    "2. **Deductible/Rider Check**:",
                    "   * The case study record shows the standard **$500.00 Comprehensive deductible** was applied to the overall claim.",
                    "   * However, Sarah Jenkins' active **Zero-Deductible Glass** rider (*Endorsement_Windshield_Zero_Deductible.docx*) waives the Comprehensive deductible specifically for windshield/safety glass replacement. If the $500 deductible was drawn entirely from the windshield line rather than the non-glass repairs, this may be a rider mis-application worth a supplement review.",
                    "",
                    "**Summary Recommendation**: Approve the estimate as-is; flag the deductible allocation against the Zero-Deductible Glass rider for adjuster review."
                ])
            else: # letter / general
                logs.append("⚙️ [Tool Exec] policy_search: Found Case_Study_Hail_Damage_PlanA.pdf, Endorsement_Windshield_Zero_Deductible.docx")
                logs.append("🧮 [Calculator Tool] Payout Math: $3,200 PDR + $2,400 Repainting + $1,200 Windshield - $500 Comprehensive deductible = $6,300.")
                answer_parts.extend([
                    "### 📝 Claim Decision & Payout Settlement Letter: Sarah Jenkins",
                    "**Date**: July 16, 2026",
                    "**Claim ID**: #2026-10492",
                    "**Insured**: Sarah Jenkins",
                    "**Vehicle**: 2024 Ford F-150 SuperCrew",
                    "",
                    "Dear Sarah Jenkins,",
                    "",
                    "We have completed our audit of the hail and windshield damage repair estimate submitted by Apex Auto Body for your 2024 Ford F-150.",
                    "",
                    "**Settlement Calculations**:",
                    "*   **Total Approved Repairs**: $6,800.00",
                    "    *   *Paintless Dent Repair (PDR - 42 dents)*: $3,200.00 (Approved)",
                    "    *   *Bodywork & Repainting (passenger doors)*: $2,400.00 (Approved — distinct from PDR, not duplicative)",
                    "    *   *Windshield Replacement*: $1,200.00 (Approved)",
                    "*   **Deductible Applied**: -$500.00 (Standard Comprehensive deductible)",
                    "*   **Net Settlement Payout**: **$6,300.00** (Paid directly to Apex Auto Body)",
                    "",
                    "Note: Your policy includes a Zero-Deductible Glass rider, which waives the Comprehensive deductible for windshield replacement specifically. We are reviewing whether the $500.00 deductible above should instead be allocated only to the non-glass repairs; you may see a supplemental adjustment.",
                    "",
                    "Sincerely,",
                    "**Claims Adjuster Copilot**",
                    "*Citations: Case_Study_Hail_Damage_PlanA.pdf, Endorsement_Windshield_Zero_Deductible.docx*"
                ])

        elif claim_id == "#2026-30291": # David Chen
            if audit_type == "labor":
                logs.append("⚙️ [Tool Exec] policy_search: Found SOP_Auto_Repair_Labor_Rates.pdf")
                logs.append("🧮 [Calculator Tool] Checking Elite Fleet Repair mechanical rate: $120/hr > Nevada cap of $110/hr.")
                logs.append("🧮 [Calculator Tool] Labor Over-billing Math: 8 hrs * $10 difference = $80.00 total over-billing.")
                answer_parts.extend([
                    "### ⚡ Regional Labor Rate & Estimate Audit: David Chen (#2026-30291)",
                    "**Status: VIOLATION (rates exceed limits)**",
                    "",
                    "1. **Labor Rate Auditing**:",
                    "   * **Mechanical rate**: Elite Fleet Repair (Las Vegas, NV) charges **$120.00/hr** for catalytic converter and infotainment console labor.",
                    "   * Under *Regional_Labor_Rates_2026.xlsx*, the maximum allowed mechanical rate for Nevada is **$110.00/hr**.",
                    "   * The estimate charges 2.0 hrs (catalytic) and 6.0 hrs (infotainment) at $120/hr, resulting in a **$80.00 labor rate over-billing**.",
                    "2. **Operation Hour Limits**:",
                    "   * Converter replacement (2.0 hrs) and console replacement (6.0 hrs) comply with standard repair labor time guides.",
                    "",
                    "**Summary Recommendation**: Reject estimate. Demand labor rates be reduced from $120/hr to $110/hr."
                ])
            elif audit_type == "oem":
                logs.append("⚙️ [Tool Exec] policy_search: Found Endorsement_Custom_Audio_Visual.docx")
                logs.append("🧮 [Calculator Tool] Custom Equipment Value: Wheels ($2,400) + Infotainment ($3,500) = $5,900.")
                logs.append("🧮 [Calculator Tool] Custom Cap Check: $5,900 exceeds endorsement policy limit of $3,500.00 per occurrence. Excluded value: $2,400.")
                answer_parts.extend([
                    "### 🔧 OEM Parts Rider Audit: David Chen (#2026-30291)",
                    "**Status: PARTIALLY APPROVED (Limit applied)**",
                    "",
                    "1. **Endorsement Checklist**:",
                    "   * Active Endorsements: **OEM Parts Guarantee**, **Custom Equipment ($3.5k limit)**.",
                    "2. **Custom Parts Audit**:",
                    "   * Stolen equipment: **Enkei Wheels ($2,400.00)** and **Alpine Infotainment Console ($3,500.00)**.",
                    "   * Under *Endorsement_Custom_Audio_Visual.docx*, custom equipment (aftermarket stereos, amplifiers, and screens not factory-installed) is capped at **$3,500.00 per occurrence**, subject to 10% annual depreciation from install date and proof of purchase.",
                    "   * The actual parts value of **$5,900.00** exceeds the per-occurrence limit by **$2,400.00**, which must be paid out-of-pocket by the claimant.",
                    "3. **Exhaust OEM Audit**:",
                    "   * The catalytic converter is written as OEM ($1,500). Although the vehicle is a 2022 (4 years old), NV emissions laws mandate EPA-compliant OEM converters. Approved.",
                    "",
                    "**Summary Recommendation**: Limit custom equipment settlement to $3,500.00 per occurrence."
                ])
            elif audit_type == "fraud":
                logs.append("⚙️ [Tool Exec] policy_search: Found SOP_Claims_Fraud_Red_Flags.pdf")
                logs.append("⚙️ [Tool Exec] dossier_search: Found custom_equipment_receipts_Chen.xlsx and recovery photos")
                logs.append("🔍 [Reflection] Checking oxidation on passenger side key scratches: Rust present (pre-existing damage).")
                answer_parts.extend([
                    "### 🔍 SIU Fraud Red Flags Audit: David Chen (#2026-30291)",
                    "**Status: RED FLAG DETECTED (Pre-existing damage)**",
                    "",
                    "1. **Wear & Tear Investigation**:",
                    "   * The estimate lists **$600.00** to repair key scratches on the passenger side panel.",
                    "   * The investigator notes (*custom_equipment_receipts_Chen.xlsx*) and photos show the key scratches have extensive deep rust oxidation.",
                    "   * Under *SOP_Claims_Fraud_Red_Flags.pdf*, oxidized rust proves the damage is pre-existing (at least 6 months old) and did not occur during the recent theft. Key scratch repair is **Denied**.",
                    "2. **Theft Recovery Check**:",
                    "   * Police report confirms vehicle recovery with no key scratching noted on recovery scene inventory.",
                    "",
                    "**Summary Recommendation**: Deny the $600.00 key scratch repair. Proceed with theft-recovery billing."
                ])
            else: # letter / general
                logs.append("⚙️ [Tool Exec] policy_search: Found Endorsement_Custom_Audio_Visual.docx")
                logs.append("🧮 [Calculator Tool] Payout Math: $1,500 catalytic + $3,500 custom cap - $250 deductible = $4,750.")
                answer_parts.extend([
                    "### 📝 Claim Decision & Payout Settlement Letter: David Chen",
                    "**Date**: July 16, 2026",
                    "**Claim ID**: #2026-30291",
                    "**Insured**: David Chen",
                    "**Vehicle**: 2022 Honda Civic Sport",
                    "",
                    "Dear David Chen,",
                    "",
                    "We have audited the theft damage repair estimate submitted by Elite Fleet Repair for your 2022 Honda Civic Sport.",
                    "",
                    "**Settlement Calculations**:",
                    "*   **Total Approved Repairs**: $5,000.00",
                    "    *   *Catalytic Converter replacement*: $1,500.00 (Approved)",
                    "    *   *Custom Equipment (Alpine console & Enkei wheels)*: Capped at **$3,500.00 per occurrence** (actual value $5,900.00 exceeded the limit by $2,400.00, payable out-of-pocket by claimant)",
                    "    *   *Passenger side key scratch repairs*: **Denied ($0.00)**. Excluded under *SOP_Claims_Fraud_Red_Flags.pdf* as pre-existing rust oxidation.",
                    "*   **Policy Deductibles**:",
                    "    *   *Comprehensive Deductible*: -$250.00",
                    "*   **Net Settlement Payout**: **$4,750.00**",
                    "",
                    "Please note that Elite Fleet Repair's labor rate was reduced to the Nevada mechanical cap of $110/hr.",
                    "",
                    "Sincerely,",
                    "**Claims Adjuster Copilot**",
                    "*Citations: Endorsement_Custom_Audio_Visual.docx, SOP_Claims_Fraud_Red_Flags.pdf, Regional_Labor_Rates_2026.xlsx*"
                ])

        elif claim_id == "#2026-55912": # Elena Rostova
            if audit_type == "labor":
                logs.append("⚙️ [Tool Exec] policy_search: Found Regional_Labor_Rates_2026.xlsx")
                logs.append("🧮 [Calculator Tool] Checking Classic Auto Restoration mechanical rate: $110/hr == Florida cap of $110/hr.")
                logs.append("🧮 [Calculator Tool] Checking block replacement labor hours: 20.0 hours (complies with standard N20 motor swap guidelines).")
                answer_parts.extend([
                    "### ⚡ Regional Labor Rate & Estimate Audit: Elena Rostova (#2026-55912)",
                    "**Status: COMPLIANT (estimate approved)**",
                    "",
                    "1. **Labor Rate Auditing**:",
                    "   * **Mechanical rate**: Classic Auto Restoration charges **$110.00/hr**, which matches the **$110.00/hr Florida Mechanical Cap** in *Regional_Labor_Rates_2026.xlsx*.",
                    "2. **Operation Hour Limits**:",
                    "   * The request for **20.0 hours** to pull and replace the engine block complies with standard labor times for BMW N20 long block swaps (PASSED).",
                    "",
                    "**Summary Recommendation**: Labor rates are compliant. Proceed to coverage and exclusions audit."
                ])
            elif audit_type == "oem":
                logs.append("⚙️ [Tool Exec] policy_search: Found Rider_OEM_Parts_Guarantee.pdf")
                logs.append("🧮 [Calculator Tool] Checking vehicle age: 2020 BMW is 6 years old (exceeds the 5-year OEM Parts Guarantee threshold).")
                logs.append("🧮 [Calculator Tool] Parts replacement limit: Deny OEM block ($9,500); Cap block parts costs at LKQ salvage rate of $5,200.")
                answer_parts.extend([
                    "### 🔧 OEM Parts Rider Audit: Elena Rostova (#2026-55912)",
                    "**Status: DENIED (Standard policy limits apply)**",
                    "",
                    "1. **Endorsement Checklist**:",
                    "   * The policy contains **no** OEM Parts Guarantee rider (Plan A).",
                    "   * Vehicle is a 2020 BMW (6 years old).",
                    "   * Under standard policy guidelines, adjusters must write for LKQ (Like Kind and Quality) salvage engines. The shop wrote for a brand new BMW factory block ($9,500.00). Under policy rules, this is denied; LKQ engine parts are capped at **$5,200.00**.",
                    "2. **Exclusion Conflict**:",
                    "   * Note that primary block damage is excluded entirely under SIU findings, making parts choice moot.",
                    "",
                    "**Summary Recommendation**: Block replacement is denied due to exclusions; parts check is moot."
                ])
            elif audit_type == "fraud":
                logs.append("⚙️ [Tool Exec] policy_search: Found Case_Study_Engine_Hydro_Lock.pdf")
                logs.append("⚙️ [Tool Exec] dossier_search: Found telematics_log_Rostova.pdf (Speed 45 mph in standing water, followed by 3 starter crank attempts) and engine_diagnostic_report_Rostova.pdf")
                logs.append("🔍 [Reflection] Checking starter logs: 3 cranking attempts post-stall confirmed.")
                logs.append("🔍 [Reflection] Checking claim file for a DUI citation: no police report on file for this claim; no such record found.")
                answer_parts.extend([
                    "### 🔍 SIU Fraud Red Flags Audit: Elena Rostova (#2026-55912)",
                    "**Status: CRITICAL EXCLUSION DETECTED (CLAIM DENIED)**",
                    "",
                    "1. **Consequential Damage Audit**:",
                    "   * Telematics diagnostic log (*telematics_log_Rostova.pdf*) shows that after the vehicle stalled in standing water, the starter ignition button was pressed **3 separate times**; *engine_diagnostic_report_Rostova.pdf* confirms standing water in the intake and a fractured engine block consistent with hydraulic lock.",
                    "   * Under *Case_Study_Engine_Hydro_Lock.pdf*, damages resulting from attempts to restart a stalled engine in deep water are driver-induced consequential damages and are **excluded**.",
                    "2. **DUI Exclusion Check**:",
                    "   * No police report or citation record exists in this claim's file. The DUI exclusion (*DUI_Exclusion_Directive.txt*) is not applicable here — there is no evidence to invoke it, and this should not be asserted without a supporting record.",
                    "",
                    "**Summary Recommendation**: Deny claims liability in full on consequential-damage grounds; DUI exclusion not applicable absent supporting evidence."
                ])
            else: # letter / general
                logs.append("⚙️ [Tool Exec] policy_search: Found Case_Study_Engine_Hydro_Lock.pdf")
                logs.append("🧮 [Calculator Tool] Payout Math: Consequential damage exclusion applies in full. Net Payout = $0.00.")
                answer_parts.extend([
                    "### 📝 Claim Decision & Payout Settlement Letter: Elena Rostova",
                    "**Date**: July 16, 2026",
                    "**Claim ID**: #2026-55912",
                    "**Insured**: Elena Rostova",
                    "**Vehicle**: 2020 BMW 330i xDrive",
                    "",
                    "Dear Elena Rostova,",
                    "",
                    "We regret to inform you that your insurance claim for the engine damage on your 2020 BMW 330i has been **Denied** in full.",
                    "",
                    "**Reason for Denial**:",
                    "Mechanical reports and telematics records show that after the vehicle stalled in standing water, the ignition was activated three times attempting to restart it, drawing water further into the engine and fracturing the block. Under *Case_Study_Engine_Hydro_Lock.pdf*, engine damage caused by restart attempts on a water-stalled engine is driver-induced consequential damage and is excluded from coverage.",
                    "",
                    "*   **Total Settlement Payout**: **$0.00**",
                    "",
                    "Sincerely,",
                    "**Claims Adjuster Copilot**",
                    "*Citations: Case_Study_Engine_Hydro_Lock.pdf, telematics_log_Rostova.pdf, engine_diagnostic_report_Rostova.pdf*"
                ])

        else: # Generic reference lookup
            logs.append("⚙️ [Tool Exec] No active claim folder selected. Fetching global reference matches.")
            answer_parts.append(f"### [Agentic RAG Assistant]\n\nBased on your query: \"{query_text}\", here are the matching policy references in the system:\n")
            for m in matches[:2]:
                answer_parts.append(f"- **{m['filename']}**: \"{m['content'][:300]}...\"\n")

        answer = "\n".join(answer_parts)

        elapsed = (time.time() - start_time) * 1000
        logs.append(f"✅ [Agentic Coordinator] Completed reasoning cycle in {elapsed:.1f}ms")

        claim_dossier = self._get_claim_context_markdown(claim_id) if claim_id else None

        return {
            "answer": answer,
            "sources": [
                {
                    "filename": m["filename"],
                    "file_type": m["file_type"],
                    "content": m["content"],
                    "score": round(m["score"], 3)
                }
                for m in matches[:4]
            ],
            "claim_dossier": claim_dossier if claim_dossier else None,
            "engine": "simulated (agentic)",
            "pipeline_logs": logs
        }
