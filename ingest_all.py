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

def generate_pdf(filename, title, content):
    import os
    os.makedirs("stored_documents", exist_ok=True)
    file_path = os.path.join("stored_documents", filename)
    
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    
    doc = SimpleDocTemplate(file_path, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'HeaderStyle',
        parent=styles['Heading1'],
        fontSize=16,
        textColor=colors.HexColor('#002B49'), # Guidewire Jutro Blue
        spaceAfter=15
    )
    body_style = ParagraphStyle(
        'BodyStyle',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=8,
        leading=14
    )
    
    story = []
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 10))
    
    for line in content.split('\n'):
        if line.strip():
            story.append(Paragraph(line.strip(), body_style))
            
    doc.build(story)
    return file_path

def generate_excel(filename, data_rows, columns):
    import os
    import pandas as pd
    os.makedirs("stored_documents", exist_ok=True)
    file_path = os.path.join("stored_documents", filename)
    df = pd.DataFrame(data_rows, columns=columns)
    df.to_excel(file_path, index=False)
    return file_path

def copy_seeded_images():
    import os
    import glob
    import shutil
    conv_id = "e0b77ff9-d43c-47a9-b39b-68f932a3e451"
    artifacts_dir = f"C:/Users/shino/.gemini/antigravity/brain/{conv_id}"
    
    image_mappings = {
        "tesla_rear_collision": "tesla_rear_collision.png",
        "f150_hail_damage": "f150_hail_damage.png",
        "civic_smashed_window": "civic_smashed_window.png",
        "bmw_water_damage": "bmw_water_damage.png"
    }
    
    os.makedirs("stored_documents", exist_ok=True)
    
    for prefix, target_name in image_mappings.items():
        pattern = os.path.join(artifacts_dir, f"{prefix}_*.png")
        matches = glob.glob(pattern)
        if matches:
            src = matches[0]
            dest = os.path.join("stored_documents", target_name)
            shutil.copy2(src, dest)
            print(f"   Copied seed photo {src} -> {dest}")
        else:
            print(f"   ⚠️ Could not find generated seed photo for {prefix} in {artifacts_dir}")

def seed_claim_documents(vector_store, embedding_engine):
    print("\nSeeding claim-specific dossier files...")
    
    # 1. Copy generated PNG images to stored_documents
    copy_seeded_images()
    
    # 2. Compile and index PDFs, Excels, and Images
    seeds = [
        # Matthew Sterling
        {
            "claim_id": "#2026-99382",
            "filename": "police_report_Sterling.pdf",
            "type": "pdf",
            "title": "STATE OF CALIFORNIA - POLICE ACCIDENT REPORT",
            "content": "Case ID: #2026-99382\nDate: January 12, 2026\nLocation: Santa Monica Blvd, Los Angeles, CA\nReporting Officer: Sgt. M. Vance, LAPD\n\nSummary:\nVehicle 1 (2023 Tesla Model Y, driven by Matthew Sterling) was stopped at a red light on Santa Monica Blvd heading westbound. Vehicle 2 failed to stop in time and rear-ended Vehicle 1 at approximately 18 mph.\n\nInjuries:\nDriver of Vehicle 1 reported a sudden jar but declined emergency medical attention at the scene. No other injuries reported.\n\nVehicle Damage:\nVehicle 1 suffered severe rear bumper cover deformation and structural rear bumper reinforcement beam crushing. Vehicle 2 suffered extensive front engine bay crumpling and was towed from the scene.\n\nTelemetry Log Notes:\nVehicle 1 Autopilot system was active at the moment of collision. Telematics indicate 0 mph speed, brake pressure 100%, and vehicle state stationary for 8.4 seconds prior to rear-end impact."
        },
        {
            "claim_id": "#2026-99382",
            "filename": "tesla_rear_collision.png",
            "type": "image",
            "content": "Inspection Photo: Crushed and deformed rear bumper cover of Matthew Sterling's red 2023 Tesla Model Y collision damage. Taken at Caliber Collision Los Angeles."
        },
        {
            "claim_id": "#2026-99382",
            "filename": "shop_email_thread_Sterling.pdf",
            "type": "pdf",
            "title": "CALIBER COLLISION - SERVICE ADVISOR EMAIL THREAD",
            "content": "Claim ID: #2026-99382\nDate: January 15, 2026\nFrom: Service Advisor, Caliber Collision (Los Angeles)\nTo: Claims Adjuster, Auto Insurance\nSubject: Supplemental Repair Estimate Details for Tesla Model Y\n\nDear Adjuster,\n\nWe have completed our disassembly of Mr. Sterling's Tesla Model Y. In addition to the rear bumper cover, the rear motor shield is cracked and needs full replacement. The aluminum subframe is not bent, but we do need to pull the rear body panel (5.0 hours frame time) to align the tailgate properly.\n\nThe ADAS backup sensors also require standard recalibration. We have requested factory OEM parts for the motor shield and ADAS modules. Let us know if you approve this supplement."
        },
        
        # Sarah Jenkins
        {
            "claim_id": "#2026-10492",
            "filename": "f150_hail_damage.png",
            "type": "image",
            "content": "Inspection Photo: Hail dent pits on the hood of Sarah Jenkins's metallic grey 2024 Ford F-150 SuperCrew truck."
        },
        {
            "claim_id": "#2026-10492",
            "filename": "dent_photos_description_Jenkins.pdf",
            "type": "pdf",
            "title": "HAIL DAMAGE PHOTO INSPECTION LOG",
            "content": "Claim ID: #2026-10492\nInsured: Sarah Jenkins\nVehicle: 2024 Ford F-150 SuperCrew\nInspector: A. Ramirez, Claims Specialist\n\nPhotos Reviewed:\n- Photo 1: Hood surface displaying multiple hail dents (approximately 18 separate point impacts).\n- Photo 2: Roof panel showing dense cluster of hail pitting (approximately 24 point impacts). No paint fracturing observed.\n- Photo 3: Windshield passenger side displaying severe circular windshield crack (radial lines extending 4 inches). Requires replacement.\n- Photo 4: Tailgate display panel showing minor superficial dent.\n\nAdjuster Notes:\nPaintless Dent Repair (PDR) is fully applicable for hood and roof dents, as paint remains unbroken. Windshield replacement is mandatory under standard safety rules."
        },
        
        # David Chen
        {
            "claim_id": "#2026-30291",
            "filename": "police_theft_report_Chen.pdf",
            "type": "pdf",
            "title": "METROPOLITAN POLICE DEPARTMENT - THEFT/VANDALISM REPORT",
            "content": "Case ID: #2026-30291\nDate: January 18, 2026\nReporting Party: David Chen\nVehicle: 2022 Honda Civic Sport\nReporting Officer: Deputy J. Reynolds\n\nSummary:\nReporting party states his vehicle was parked in the apartment complex garage overnight. On January 18, 2026 at 07:00, he discovered the vehicle's front passenger window smashed. The dashboard center console infotainment screen had been amateurishly pried out and stolen. Additionally, the exhaust system sounded abnormally loud upon startup. Inspection underneath revealed the catalytic converter had been cleanly sawed off and stolen. No suspect information at this time."
        },
        {
            "claim_id": "#2026-30291",
            "filename": "custom_equipment_receipts_Chen.xlsx",
            "type": "excel",
            "columns": ["Item Category", "Item Description", "Store/Provider", "Purchase Date", "Cost"],
            "rows": [
                ["Wheels & Tires", "4x Enkei Sport Premium Wheels & Tires Package", "Elite Custom Auto Sound & Wheels", "2025-08-14", 2400.0],
                ["Infotainment", "Alpine Touchscreen Infotainment Console & Amp", "Elite Custom Auto Sound & Wheels", "2025-08-14", 3500.0]
            ],
            "content": "Receipt: Elite Custom Auto Sound & Wheels (Las Vegas)\nDate: August 14, 2025\nCustomer: David Chen\nPurchased Items:\n- 4x Enkei Sport Premium Wheels & Tires Package: $2,400.00 (Installed)\n- Alpine Touchscreen Infotainment Console & Amplifier System: $3,500.00 (Installed)\nTotal Invoice: $5,900.00\nPayment Method: Visa ending in 4920\nNotes: All custom equipment carries a 12-month shop warranty."
        },
        {
            "claim_id": "#2026-30291",
            "filename": "civic_smashed_window.png",
            "type": "image",
            "content": "Inspection Photo: Smashed passenger window and vandalized console dash screen in David Chen's 2022 Honda Civic Sport."
        },
        
        # Elena Rostova
        {
            "claim_id": "#2026-55912",
            "filename": "telematics_log_Rostova.pdf",
            "type": "pdf",
            "title": "BMW CONNECTEDDRIVE - TELEMATICS DIAGNOSTIC LOG",
            "content": "Claim ID: #2026-55912\nVehicle: 2020 BMW 330i xDrive\nDate of Incident: January 20, 2026\n\nTime Series Diagnostic Extract:\n- 14:32:05 - Speed: 45 mph. Throttle: 60%. Road Condition: Standing Water.\n- 14:32:08 - Sudden engine RPM drop from 2,400 to 0. Speed: 15 mph. Brake: 80%.\n- 14:32:10 - Engine State: STALLED (Hydraulic lock flag active).\n- 14:32:15 - Ignition Button Press (Starter Attempt 1). Starter Motor Current: High. RPM: 0. Result: FAIL.\n- 14:32:18 - Ignition Button Press (Starter Attempt 2). Starter Motor Current: High. RPM: 0. Result: FAIL.\n- 14:32:22 - Ignition Button Press (Starter Attempt 3). Starter Motor Current: High. RPM: 0. Result: FAIL.\n\nAdjuster Summary:\nTelematics show the vehicle drove into standing water at 45 mph. After the initial stall, the driver attempted to restart the engine 3 times, causing terminal hydro-lock block fracturing."
        },
        {
            "claim_id": "#2026-55912",
            "filename": "engine_diagnostic_report_Rostova.pdf",
            "type": "pdf",
            "title": "CLASSIC AUTO RESTORATION - MECHANIC FINDINGS",
            "content": "Claim ID: #2026-55912\nVehicle: 2020 BMW 330i xDrive\nTechnician: R. Martinez\n\nFindings:\n- Disassembled intake tract: found standing water inside the airbox and charge pipe.\n- Removed spark plugs: water ejected from cylinders 2 and 3 upon manual crank inspection.\n- Internal Check: Connecting rod in cylinder 2 is bent severely. The engine block has a hairline fracture near the starter mount, caused by attempting to compress water (hydraulic lock).\n\nRecommendation:\nComplete replacement of the engine long block assembly. Used engine cost: $9,500. Labor time: 20.0 hours. Water flushed from oil lines mandatory."
        },
        {
            "claim_id": "#2026-55912",
            "filename": "bmw_water_damage.png",
            "type": "image",
            "content": "Inspection Photo: Disassembled BMW 330i engine intake tract showing standing water and bent piston rod diagnostic inspection."
        }
    ]
    
    for s in seeds:
        file_path = None
        if s["type"] == "pdf":
            file_path = generate_pdf(s["filename"], s["title"], s["content"])
        elif s["type"] == "excel":
            file_path = generate_excel(s["filename"], s["rows"], s["columns"])
        elif s["type"] == "image":
            file_path = os.path.join("stored_documents", s["filename"])
            
        file_size = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else len(s["content"])
        file_ext = s["filename"].split(".")[-1].lower()
        
        vector_store.add_document(
            filename=s["filename"],
            file_type=file_ext,
            file_size=file_size,
            text=s["content"],
            embedding_engine=embedding_engine,
            claim_id=s["claim_id"],
            file_path=file_path
        )
        print(f"   Indexed & seeded high-fidelity {s['filename']} for claim {s['claim_id']}.")

if __name__ == "__main__":
    ingest_all()
