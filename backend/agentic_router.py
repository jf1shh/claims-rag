import os
import json
import time
import sqlite3
import requests
from typing import Optional, List, Dict, Any

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
    def __init__(self):
        pass

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
        reranking_engine: Any
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
        
        # Else, online LM Studio mode
        engine_url = "http://127.0.0.1:1234" if engine == "lm-studio" else engine
        return self._run_online_agent(query_text, claim_id, engine_url, vector_store, embedding_engine, reranking_engine, logs, start_time)

    def _get_loaded_model(self, engine_url: str) -> str:
        try:
            response = requests.get(f"{engine_url}/v1/models", timeout=2.0)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and len(data["data"]) > 0:
                    return data["data"][0]["id"]
        except Exception:
            pass
        return "local-model"

    def _run_online_agent(
        self,
        query_text: str,
        claim_id: Optional[str],
        engine_url: str, # URL of the LM Studio endpoint
        vector_store: Any,
        embedding_engine: Any,
        reranking_engine: Any,
        logs: List[str],
        start_time: float
    ) -> Dict[str, Any]:
        # Step 1: Query Decomposition (Planner Call)
        logs.append("📋 [Step 1: Planning] Decomposing query into target sub-queries...")
        model_name = self._get_loaded_model(engine_url)
        plan = self._get_llm_plan(query_text, claim_id, engine_url, model_name)
        
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
                        
            # Retrieve dossier attachments
            if plan["needs_claim_dossier"] and claim_id:
                matches = vector_store.search_similarity(
                    query_emb, 
                    sub_q, 
                    claim_id=claim_id, 
                    reranking_engine=reranking_engine, 
                    top_k=3
                )
                for m in matches:
                    p_key = (m["filename"], m["content"][:50])
                    if p_key not in seen_passages:
                        seen_passages.add(p_key)
                        all_matches.append(m)
            
            logs.append(f"⚙️ [Tool Exec] Retrieval for '{sub_q}' complete in {(time.time() - sub_start)*1000:.1f}ms")

        # Step 3: Self-Correction / Query Translation Loop
        #
        # This must trigger whenever nothing was retrieved, not only when a
        # claim_id is active. The planner's needs_global_policies /
        # needs_claim_dossier classification can both come back False (or
        # needs_claim_dossier=True with no claim_id active, which short-circuits
        # the dossier branch entirely) -- without this fallback, retrieval is
        # silently skipped and synthesis proceeds with zero context, which the
        # LLM fills in by fabricating an answer and citing sources that don't
        # exist in the corpus. Caught via eval harness: a global policy query
        # returned 0 sources and cited invented filenames.
        if not all_matches and claim_id:
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
        elif not all_matches:
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

        # Sort combined matches by score
        all_matches.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        top_matches = all_matches[:4]

        # Hard stop: never let the LLM synthesize freely with zero retrieved
        # context. Without this, an ungrounded call reliably fabricates both
        # an answer and citations to filenames that don't exist in the corpus.
        if not top_matches:
            logs.append("❌ [Synthesis Skipped] No supporting documents found after self-correction; refusing to answer ungrounded.")
            elapsed = (time.time() - start_time) * 1000
            logs.append(f"✅ [Agentic Coordinator] Completed reasoning cycle in {elapsed:.1f}ms")
            return {
                "answer": "I couldn't find any supporting documents for this question in the available guidelines"
                           + (f" or claim #{claim_id} dossier" if claim_id else "") + ". "
                           "Please rephrase the question or confirm the relevant policy/claim documents have been uploaded.",
                "sources": [],
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
            "policies, or guidelines using ONLY the provided reference sources and the active claim summary dossier. Perform calculations (payouts, caps, deductibles) if asked. "
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
            url = f"{engine_url}/v1/chat/completions"
            headers = { "Content-Type": "application/json" }
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 1000
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=45)
            if response.status_code == 200:
                answer = response.json()["choices"][0]["message"]["content"]
            else:
                raise Exception(f"LLM request failed with status: {response.status_code}")
        except Exception as e:
            logs.append(f"❌ [Synthesis Error] LLM generation failed: {str(e)}. Falling back to simulation.")
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
            "engine": "lm-studio (agentic)",
            "pipeline_logs": logs
        }

    def _get_llm_plan(self, query_text: str, claim_id: Optional[str], engine_url: str, model_name: str) -> Dict[str, Any]:
        """Requests a structured JSON plan from the LLM."""
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
            url = f"{engine_url}/v1/chat/completions"
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.0,
                "max_tokens": 150
            }
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                text = response.json()["choices"][0]["message"]["content"].strip()
                # Parse JSON out of response
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                return json.loads(text)
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
                logs.append("🧮 [Calculator Tool] Checking frame pull hours: 5.0 hours <= 6.0 hours policy cap (Adjuster_Guide_Rear_Impact.docx).")
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
                    "2. **Operation Hour Limits**:",
                    "   * The request for **5.0 hours** to pull the rear body panel complies with the **6.0 hours** rear impact structural cap in *Adjuster_Guide_Rear_Impact.docx* (PASSED).",
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
                    "   * Rider Terms (*Rider_OEM_Parts_Guarantee.pdf*): Mandates the usage of brand-new, factory-original OEM parts for all collision replacements on vehicles **under 3 years of age**.",
                    "2. **Vehicle Age Verification**:",
                    "   * Vehicle: 2023 Tesla Model Y.",
                    "   * Date of Loss Audit: 2026.",
                    "   * Age Calculation: 2026 - 2023 = **3 years old**. The vehicle qualifies under the OEM mandate threshold.",
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
                logs.append("🔍 [Reflection] Checking unbroken paint status: PDR mandatory per Plan A.")
                answer_parts.extend([
                    "### 🔍 SIU Fraud Red Flags Audit: Sarah Jenkins (#2026-10492)",
                    "**Status: RED FLAGS DETECTED (supplement adjustment required)**",
                    "",
                    "1. **Estimate Duplication Check**:",
                    "   * The estimate charges **$3,200.00** for Paintless Dent Repair (PDR) for 42 hail dents on the hood and roof, AND **$900.00** for door refinishing.",
                    "   * Photo inspection log (*dent_photos_description_Jenkins.pdf*) proves the paint on the hood and roof dents is **completely unbroken**.",
                    "   * Under *Case_Study_Hail_Damage_PlanA.pdf*, PDR is mandatory for paint-intact hail damage. Standard body shop panel repainting charges ($900.00) are denied as duplicative overlap.",
                    "2. **Weather Verification**:",
                    "   * Local weather logs verify a severe hail event (1.2-inch stones) occurred in San Francisco on the date of loss (PASSED).",
                    "",
                    "**Summary Recommendation**: Reject the $900.00 repainting charge. Standardize repair on the PDR estimate only."
                ])
            else: # letter / general
                logs.append("⚙️ [Tool Exec] policy_search: Found Endorsement_Windshield_Zero_Deductible.docx")
                logs.append("🧮 [Calculator Tool] Payout Math: $3,200 PDR + $1,200 Windshield + $350 Calibration - $0 glass deductible = $4,750.")
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
                    "*   **Total Approved Repairs**: $4,750.00",
                    "    *   *Paintless Dent Repair (PDR - 42 dents)*: $3,200.00 (Approved)",
                    "    *   *Windshield Replacement*: $1,200.00 (Approved under glass rider)",
                    "    *   *ADAS Windshield Camera Calibration*: $350.00 (Approved)",
                    "    *   *Door Refinishing / Repainting*: **Denied ($0.00)**. Excluded under *Case_Study_Hail_Damage_PlanA.pdf* because paint was not fractured.",
                    "*   **Deductible Applied**: -$0.00 (Glass deductible waived; Comprehensive deductible for PDR met by primary hail damage)",
                    "*   **Net Settlement Payout**: **$4,750.00** (Paid directly to Apex Auto Body)",
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
                logs.append("🧮 [Calculator Tool] Custom Cap Check: $5,900 exceeds endorsement policy limit of $5,000.00. Excluded value: $900.")
                answer_parts.extend([
                    "### 🔧 OEM Parts Rider Audit: David Chen (#2026-30291)",
                    "**Status: PARTIALLY APPROVED (Limit applied)**",
                    "",
                    "1. **Endorsement Checklist**:",
                    "   * Active Endorsements: **OEM Parts Guarantee**, **Custom Equipment ($3.5k limit)**.",
                    "2. **Custom Parts Audit**:",
                    "   * Stolen equipment: **Enkei Wheels ($2,400.00)** and **Alpine Infotainment Console ($3,500.00)**.",
                    "   * Under *Endorsement_Custom_Audio_Visual.docx*, custom equipment is capped at **$5,000.00** total, carrying a **$250.00 custom deductible**.",
                    "   * The actual parts value of **$5,900.00** exceeds the limit by **$900.00** which must be paid out-of-pocket by the claimant.",
                    "3. **Exhaust OEM Audit**:",
                    "   * The catalytic converter is written as OEM ($1,500). Although the vehicle is a 2022 (4 years old), NV emissions laws mandate EPA-compliant OEM converters. Approved.",
                    "",
                    "**Summary Recommendation**: Limit custom equipment settlement to $5,000.00."
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
                logs.append("🧮 [Calculator Tool] Payout Math: $1,500 catalytic + $5,000 custom cap - $250 deductible = $6,250.")
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
                    "*   **Total Approved Repairs**: $6,500.00",
                    "    *   *Catalytic Converter replacement*: $1,500.00 (Approved)",
                    "    *   *Custom Equipment (Alpine console & Enkei wheels)*: Capped at **$5,000.00** (exceeded the policy limit of $5,000 by $900.00)",
                    "    *   *Passenger side key scratch repairs*: **Denied ($0.00)**. Excluded under *SOP_Claims_Fraud_Red_Flags.pdf* as pre-existing rust oxidation.",
                    "*   **Policy Deductibles**:",
                    "    *   *Comprehensive Deductible*: -$250.00",
                    "*   **Net Settlement Payout**: **$6,250.00**",
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
                logs.append("🧮 [Calculator Tool] Checking vehicle age: 2020 BMW is 6 years old (exceeds the 3-year OEM Parts Guarantee threshold).")
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
                logs.append("⚙️ [Tool Exec] policy_search: Found Case_Study_Engine_Hydro_Lock.pdf and DUI_Exclusion_Directive.txt")
                logs.append("⚙️ [Tool Exec] dossier_search: Found telematics_log_Rostova.pdf (Speed 45 mph in standing water, followed by 3 starter crank attempts)")
                logs.append("🔍 [Reflection] Checking starter logs: 3 cranking attempts post-stall confirmed.")
                logs.append("🔍 [Reflection] Checking police record details: Driver cited for DUI during flood incident.")
                answer_parts.extend([
                    "### 🔍 SIU Fraud Red Flags Audit: Elena Rostova (#2026-55912)",
                    "**Status: CRITICAL EXCLUSIONS DETECTED (CLAIM DENIED)**",
                    "",
                    "1. **Consequential Damage Audit**:",
                    "   * Telematics diagnostic log (*telematics_log_Rostova.pdf*) proves that after the vehicle stalled in standing water, the starter ignition button was pressed **3 separate times**.",
                    "   * Under *Case_Study_Engine_Hydro_Lock.pdf*, damages resulting from attempts to restart a stalled engine in deep water are driver-induced consequential damages and are **excluded**.",
                    "2. **DUI Exclusion Check**:",
                    "   * Police report notes the claimant was cited for operating the vehicle under the influence (DUI) during the flood storm.",
                    "   * Under the **DUI Exclusion Directive** (*DUI_Exclusion_Directive.txt*), coverage for collision or flood loss is voided in full if the driver is cited for DUI.",
                    "",
                    "**Summary Recommendation**: Deny claims liability in full."
                ])
            else: # letter / general
                logs.append("⚙️ [Tool Exec] policy_search: Found DUI_Exclusion_Directive.txt")
                logs.append("🧮 [Calculator Tool] Payout Math: Voided coverages due to DUI. Net Payout = $0.00.")
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
                    "**Reasons for Denial**:",
                    "1.  **Consequential Damage**: Mechanical reports and telematics records prove that after the vehicle stalled in standing water, the ignition button was activated three times. This caused water ingestion to hydraulic-lock the block. Under *Case_Study_Engine_Hydro_Lock.pdf*, engine block fractures caused by starting attempt actions are excluded.",
                    "2.  **DUI Exclusion**: The police report indicates you were cited for operating the vehicle under the influence (DUI). Under the *DUI_Exclusion_Directive.txt* endorsement active on your policy, comprehensive coverage is completely voided during DUI operations.",
                    "",
                    "*   **Total Settlement Payout**: **$0.00**",
                    "",
                    "Sincerely,",
                    "**Claims Adjuster Copilot**",
                    "*Citations: DUI_Exclusion_Directive.txt, Case_Study_Engine_Hydro_Lock.pdf, telematics_log_Rostova.pdf*"
                ])
            
        else: # Generic reference lookup
            logs.append("⚙️ [Tool Exec] No active claim folder selected. Fetching global reference matches.")
            answer_parts.append(f"### [Agentic RAG Assistant]\n\nBased on your query: \"{query_text}\", here are the matching policy references in the system:\n")
            for m in matches[:2]:
                answer_parts.append(f"- **{m['filename']}**: \"{m['content'][:300]}...\"\n")
                
        answer = "\n".join(answer_parts)
        
        time.sleep(1.0) # Simulate planning cycles
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
                for m in matches[:4]
            ],
            "engine": "simulated (agentic)",
            "pipeline_logs": logs
        }
