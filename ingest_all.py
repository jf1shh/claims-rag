import os
import sys
import time

# Add parent directory to path so we can import from backend
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from backend.rag_engine import DocumentParser, TextChunker, EmbeddingEngine, SQLiteVectorStore

def ingest_all():
    print("=== Local RAG Batch Ingestion Script ===")
    
    # 1. Initialize Engines
    print("Initializing engines...")
    vector_store = SQLiteVectorStore()
    embedding_engine = EmbeddingEngine()
    
    # Clean up DB for fresh start
    print("Clearing existing records for a clean batch index...")
    conn = vector_store.db_path
    import sqlite3
    db = sqlite3.connect(conn)
    c = db.cursor()
    try:
        c.execute("DROP TABLE IF EXISTS parent_chunks_fts")
        c.execute("DROP TABLE IF EXISTS child_chunks")
        c.execute("DROP TABLE IF EXISTS parent_chunks")
        c.execute("DROP TABLE IF EXISTS documents")
    except sqlite3.OperationalError:
        pass # Table might not exist on first initialization
    db.commit()
    db.close()
    
    # Re-initialize vector store tables
    vector_store._init_db()
    print("Database cleared and schema initialized.")

    folder_path = "sample_guidelines"
    if not os.path.exists(folder_path):
        print(f"Error: Folder '{folder_path}' not found.")
        return
        
    files = [f for f in os.listdir(folder_path) if f.split('.')[-1].lower() in ["pdf", "docx", "xlsx", "xls", "txt"]]
    print(f"Found {len(files)} files to index inside '{folder_path}'.\n")
    
    total_start = time.time()
    
    for idx, filename in enumerate(files):
        file_path = os.path.join(folder_path, filename)
        file_ext = filename.split('.')[-1].lower()
        file_size = os.path.getsize(file_path)
        print(f"[{idx+1}/{len(files)}] Processing {filename} ({file_size / 1024:.1f} KB)...")
        
        start = time.time()
        
        try:
            # Parse text
            if file_ext == "txt":
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            else:
                text = DocumentParser.parse(file_path, file_ext)
                
            if not text.strip():
                print(f"   ⚠️ Warning: Document '{filename}' is empty, skipping.")
                continue
                
            # Save to SQLite Vector Store
            doc_id, parent_count = vector_store.add_document(
                filename=filename,
                file_type=file_ext,
                file_size=file_size,
                text=text,
                embedding_engine=embedding_engine
            )
            
            elapsed = time.time() - start
            print(f"   Indexed: {parent_count} parent chunks (with child embeddings) in {elapsed:.2f} seconds.")
            
        except Exception as e:
            print(f"   ❌ Error processing '{filename}': {str(e)}")
            
    total_elapsed = time.time() - total_start
    print(f"\n=== Batch Ingestion Complete! ===")
    print(f"Indexed {len(vector_store.get_all_documents())} global documents successfully.")
    print(f"Total processing time: {total_elapsed:.2f} seconds.")
    
    # Run claim file seeding
    seed_claim_documents(vector_store, embedding_engine)

def seed_claim_documents(vector_store, embedding_engine):
    print("\nSeeding claim-specific dossier files...")
    
    seeds = [
        {
            "claim_id": "#2026-99382",
            "filename": "police_accident_report.txt",
            "text": "STATE OF CALIFORNIA - POLICE ACCIDENT REPORT\nClaim ID: #2026-99382\nDate of Incident: January 12, 2026\nOfficer Name: Sergeant M. Vance\nSummary: Vehicle 1 (2023 Tesla Model Y, driven by Matthew Sterling) was stopped at a red light on Santa Monica Blvd. Vehicle 2 failed to stop and rear-ended Vehicle 1 at approximately 18 mph. Driver of Vehicle 1 reported a sudden jar but was not injured. Vehicle 1 suffered severe rear bumper cover deformation and rear bumper reinforcement beam crushing. Vehicle 2 was towed.\nTelemetry Log Notes: Vehicle 1 Autopilot was active at the moment of collision. Telematics show 0 mph speed, brake pressure 100%, and vehicle state stationary for 8.4 seconds prior to rear-end impact."
        },
        {
            "claim_id": "#2026-99382",
            "filename": "shop_email_thread.txt",
            "text": "Email Thread: Matthew Sterling Claim #2026-99382\nFrom: Service Advisor, Caliber Collision (Los Angeles)\nTo: Adjuster, Auto Insurance\nSubject: Re: Repair estimate for 2023 Tesla Model Y\nDate: January 15, 2026\n\nAdjuster,\nWe have completed our disassembly of Mr. Sterling's Tesla Model Y. In addition to the rear bumper cover, the rear motor shield is cracked and needs full replacement. The aluminum subframe is not bent, but we do need to pull the rear body panel (5.0 hours frame time) to align the tailgate properly. The ADAS backup sensors also require standard recalibration. We have requested factory OEM parts for the motor shield and ADAS modules. Let us know if you approve this supplement."
        },
        {
            "claim_id": "#2026-10492",
            "filename": "dent_photos_description.txt",
            "text": "Claim ID: #2026-10492 - Photo Inspection Log\nInsured: Sarah Jenkins\nVehicle: 2024 Ford F-150 SuperCrew\nPhotos Reviewed:\n- Photo 1: Hood surface displaying multiple hail dents (approximately 18 separate point impacts).\n- Photo 2: Roof panel showing dense cluster of hail pitting (approximately 24 point impacts). No paint fracturing observed.\n- Photo 3: Windshield passenger side displaying severe circular windshield crack (radial lines extending 4 inches). Requires replacement.\n- Photo 4: Tailgate display panel showing minor superficial dent.\nAdjuster Notes: PDR (Paintless Dent Repair) is fully applicable for hood and roof dents, as paint remains unbroken. Windshield replacement is mandatory under standard safety rules."
        },
        {
            "claim_id": "#2026-30291",
            "filename": "police_theft_report.txt",
            "text": "METROPOLITAN POLICE DEPARTMENT - VEHICLE THEFT/VANDALISM REPORT\nCase ID: #2026-30291\nReporting Party: David Chen\nVehicle: 2022 Honda Civic Sport\nOfficer Name: Deputy J. Reynolds\nSummary: Reporting party states his vehicle was parked in the apartment complex garage overnight. On January 18, 2026 at 07:00, he discovered the vehicle's front passenger window smashed. The dashboard center console infotainment screen had been amateurishly pried out and stolen. Additionally, the exhaust system sounded abnormally loud upon startup. Inspection underneath revealed the catalytic converter had been cleanly sawed off and stolen. No suspect information at this time."
        },
        {
            "claim_id": "#2026-30291",
            "filename": "custom_equipment_receipts.txt",
            "text": "Receipt: Elite Custom Auto Sound & Wheels (Las Vegas)\nDate: August 14, 2025\nCustomer: David Chen\nPurchased Items:\n- 4x Enkei Sport Premium Wheels & Tires Package: $2,400.00 (Installed)\n- Alpine Touchscreen Infotainment Console & Amplifier System: $3,500.00 (Installed)\nTotal Invoice: $5,900.00\nPayment Method: Visa ending in 4920\nNotes: All custom equipment carries a 12-month shop warranty."
        },
        {
            "claim_id": "#2026-55912",
            "filename": "telematics_log.txt",
            "text": "Vehicle Telematics Diagnostic Log\nClaim ID: #2026-55912\nVehicle: 2020 BMW 330i xDrive\nDate of Incident: January 20, 2026\nTime Series Extract:\n- 14:32:05 - Speed: 45 mph. Throttle: 60%. Road Condition: Standing Water.\n- 14:32:08 - Sudden engine RPM drop from 2,400 to 0. Speed: 15 mph. Brake: 80%.\n- 14:32:10 - Engine State: STALLED (Hydraulic lock flag active).\n- 14:32:15 - Ignition Button Press (Starter Attempt 1). Starter Motor Current: High. RPM: 0. Result: FAIL.\n- 14:32:18 - Ignition Button Press (Starter Attempt 2). Starter Motor Current: High. RPM: 0. Result: FAIL.\n- 14:32:22 - Ignition Button Press (Starter Attempt 3). Starter Motor Current: High. RPM: 0. Result: FAIL.\nAdjuster Summary: Telematics show the vehicle drove into standing water at 45 mph. After the initial stall, the driver attempted to restart the engine 3 times, causing terminal hydro-lock block fracturing."
        },
        {
            "claim_id": "#2026-55912",
            "filename": "engine_diagnostic_report.txt",
            "text": "Diagnostic Report: Classic Auto Restoration (Orlando, FL)\nClaim ID: #2026-55912\nVehicle: 2020 BMW 330i xDrive\nTechnician: R. Martinez\nFindings:\n- Disassembled intake tract: found standing water inside the airbox and charge pipe.\n- Removed spark plugs: water ejected from cylinders 2 and 3 upon manual crank inspection.\n- Internal Check: Connecting rod in cylinder 2 is bent severely. The engine block has a hairline fracture near the starter mount, caused by attempting to compress water (hydraulic lock).\n- Recommendation: Complete replacement of the engine long block assembly. Used engine cost: $9,500. Labor time: 20.0 hours. Water flushed from oil lines mandatory."
        }
    ]
    
    for s in seeds:
        vector_store.add_document(
            filename=s["filename"],
            file_type="txt",
            file_size=len(s["text"]),
            text=s["text"],
            embedding_engine=embedding_engine,
            claim_id=s["claim_id"]
        )
        print(f"   Seeded {s['filename']} for claim {s['claim_id']}.")

if __name__ == "__main__":
    ingest_all()
