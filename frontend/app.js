// App State
let currentEngine = 'lm-studio';
let documents = [];
let backendStatus = null;

// Auth (Phase 4.1): the access token / API key is stored in localStorage and
// attached to every API request. A 401 shows the login gate; signing in
// stores the credential and reloads, which restores the current page
// (returnTo) now that the app is authenticated.
let authToken = sessionStorage.getItem('auth_token') || null;
localStorage.removeItem('auth_token');
let authKind = sessionStorage.getItem('auth_kind') || 'bearer';
let loginGateShown = false;

function apiFetch(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (authToken) {
        if (authKind === 'api-key') headers['X-API-Key'] = authToken;
        else headers['Authorization'] = `Bearer ${authToken}`;
    }
    return fetch(path, { ...options, headers }).then(response => {
        if (response.status === 401 && !path.startsWith('/api/auth/')) {
            showLoginGate();
        }
        return response;
    });
}

// Fetches a binary document with the auth header and returns an object URL,
// so downloads/PDFs/images work when the API requires a token (a plain
// window.open/<img src> navigation cannot carry the Authorization header).
async function fetchBlobUrl(path) {
    const response = await apiFetch(path);
    if (!response.ok) throw new Error(`Failed to load document (HTTP ${response.status})`);
    const blob = await response.blob();
    return URL.createObjectURL(blob);
}

function showLoginGate() {
    if (loginGateShown) return;
    loginGateShown = true;
    const modal = document.getElementById('login-modal');
    if (modal) { modal.style.display = 'flex'; document.getElementById('login-token-input')?.focus(); }
}

function hideLoginGate() {
    loginGateShown = false;
    const modal = document.getElementById('login-modal');
    if (modal) modal.style.display = 'none';
}

async function checkAuth() {
    // /api/auth/me resolves the authenticated principal. In development the
    // server returns the explicit local identity (no login needed); under
    // OIDC/service accounts a missing/invalid credential 401s and the login
    // gate blocks the app until the user signs in.
    try {
        const response = await apiFetch('/api/auth/me');
        if (response.ok) {
            const me = await response.json();
            const label = document.getElementById('label-auth');
            if (label) {
                label.innerText = me.is_development_identity
                    ? `Signed in (dev): ${me.subject}`
                    : `Signed in: ${me.subject} @ ${me.tenant_id}`;
            }
            const chip = document.getElementById('status-auth');
            const signOut = document.getElementById('sign-out-btn');
            if (chip) chip.style.display = 'flex';
            if (signOut) signOut.style.display = 'inline-block';
            return true;
        }
    } catch (error) {
        console.error('Auth check failed:', error);
    }
    showLoginGate();
    return false;
}

function setupLoginGate() {
    const submitBtn = document.getElementById('login-submit-btn');
    const input = document.getElementById('login-token-input');
    const errorEl = document.getElementById('login-error');
    const signOutBtn = document.getElementById('sign-out-btn');

    if (submitBtn && input) {
        const attemptLogin = async () => {
            const value = input.value.trim();
            if (!value) {
                if (errorEl) errorEl.innerText = 'Enter a token or API key.';
                if (errorEl) errorEl.style.display = 'block';
                return;
            }
            authToken = value;
            authKind = document.getElementById('login-kind').value;
            try {
                const response = await apiFetch('/api/auth/me');
                if (!response.ok) throw new Error('Credential rejected for this deployment.');
            } catch (error) {
                authToken = null;
                errorEl.textContent = error.message;
                errorEl.style.display = 'block';
                return;
            }
            sessionStorage.setItem('auth_kind', authKind);
            sessionStorage.setItem('auth_token', value);
            hideLoginGate();
            // Return to where the user was: reload restores the current page,
            // now authenticated (the 401 that opened the gate is gone).
            window.location.reload();
        };
        submitBtn.addEventListener('click', attemptLogin);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') attemptLogin();
        });
    }

    if (signOutBtn) {
        signOutBtn.addEventListener('click', () => {
            sessionStorage.removeItem('auth_token');
            authToken = null;
            window.location.reload();
        });
    }
}

// Claims Database -- fetched from /api/claims (backend/agentic_router.py's
// CLAIMS_DATA), the same source of truth the agentic router grounds
// claim-scoped answers in, so this can't drift out of sync with it.
let CLAIMS_DATA = [];
let activeCase = null;

function slugifyStatus(status) {
    return (status || '').toLowerCase().replace(/\s+/g, '-');
}

async function fetchClaims() {
    const response = await apiFetch('/api/claims');
    if (!response.ok) throw new Error('Failed to fetch claims');
    CLAIMS_DATA = await response.json();
    CLAIMS_DATA.forEach(c => { c.statusClass = slugifyStatus(c.status); });
    activeCase = CLAIMS_DATA[0];
}

// DOM Elements
const browseBtn = document.getElementById('browse-btn');
const fileInput = document.getElementById('file-input');
const uploadZone = document.getElementById('upload-zone');
const uploadProgressContainer = document.getElementById('upload-progress-container');
const uploadProgressFill = document.getElementById('upload-progress-fill');
const uploadProgressStatus = document.getElementById('upload-progress-status');
const documentList = document.getElementById('document-list');
const emptyDocsState = document.getElementById('empty-docs-state');
const docCountBadge = document.getElementById('doc-count');

const chatMessages = document.getElementById('chat-messages');
const chatForm = document.getElementById('chat-form');
const queryInput = document.getElementById('query-input');
const sendBtn = document.getElementById('send-btn');
const stopBtn = document.getElementById('stop-btn');
const chatWelcome = document.getElementById('chat-welcome');

const traceTimeline = document.getElementById('trace-timeline');
const vizSystemPrompt = document.getElementById('viz-system-prompt');
const vizUserPrompt = document.getElementById('viz-user-prompt');

const sourceModal = document.getElementById('source-modal');
const modalTitle = document.getElementById('modal-title');
const modalFilename = document.getElementById('modal-filename');
const modalScore = document.getElementById('modal-score');
const modalContent = document.getElementById('modal-content');
const modalClose = document.getElementById('modal-close');
const modalViewFullBtn = document.getElementById('modal-view-full-btn');

// New Claims Portal DOM Elements
const claimsList = document.getElementById('claims-list');
const claimIdText = document.getElementById('claim-id-text');
const claimStatusBadge = document.getElementById('claim-status-badge');
const claimInsured = document.getElementById('claim-insured');
const claimVehicle = document.getElementById('claim-vehicle');
const claimFacility = document.getElementById('claim-facility');
const claimTotalEst = document.getElementById('claim-total-est');
const claimPolicyPlan = document.getElementById('claim-policy-plan');
const claimPolicyDeductible = document.getElementById('claim-policy-deductible');
const claimPolicyEndorsements = document.getElementById('claim-policy-endorsements');
const claimEstimateBody = document.getElementById('claim-estimate-body');

const btnAuditLabor = document.getElementById('btn-audit-labor');
const btnAuditOEM = document.getElementById('btn-audit-oem');
const btnAuditFraud = document.getElementById('btn-audit-fraud');
const btnAuditLetter = document.getElementById('btn-audit-letter');

const claimDocsList = document.getElementById('claim-docs-list');
const claimUploadZone = document.getElementById('claim-upload-zone');
const claimFileInput = document.getElementById('claim-file-input');

// Event Listeners
document.addEventListener('DOMContentLoaded', initializeApp);

// Initialize App
async function initializeApp() {
    setupLoginGate();
    const authenticated = await checkAuth();
    if (!authenticated) return;

    setupEngineSelection();
    setupDragAndDrop();
    setupBrowseButton();
    setupChatSuggestions();
    setupModal();
    await fetchClaims();
    setupClaimsCases();
    setupTelemetryTabs();
    setupResizableColumns();
    setupClaimUpload();

    // Initial fetch of status and documents
    await checkBackendStatus();
    await fetchDocuments();
    
    // Periodically poll backend status & database doc list every 10 seconds
    setInterval(checkBackendStatus, 10000);
}

// 1. LLM Engine Selection Logic
function setupEngineSelection() {
    const radios = document.querySelectorAll('input[name="llm-engine"]');
    radios.forEach(radio => {
        radio.addEventListener('change', (e) => {
            currentEngine = e.target.value;
            logSystemEvent(`LLM engine switched to: ${currentEngine.toUpperCase()}`);
        });
    });
}

// Check Backend Status (LM Studio, DB)
// Tracks the last observed LM Studio state so the 10-second poll only
// switches engines / writes a trace log when availability actually changes.
// Reacting on every poll spammed the trace log and silently stomped a
// manually-selected engine every 10 seconds.
let lastLmStudioActive = null;

async function checkBackendStatus() {
    try {
        const response = await apiFetch('/api/status');
        if (!response.ok) throw new Error('Status endpoint failed');

        backendStatus = await response.json();

        const lmStudioActive = !!(backendStatus && backendStatus.lm_studio && backendStatus.lm_studio.active);
        const lmStudioModels = (backendStatus && backendStatus.lm_studio && backendStatus.lm_studio.models) || [];

        const simulationEnabled = backendStatus.simulation?.enabled !== false;
        const simulationRadio = document.querySelector('input[name="llm-engine"][value="simulated"]');
        if (simulationRadio) simulationRadio.disabled = !simulationEnabled;
        updateStatusIndicator('lmstudio', lmStudioActive, lmStudioModels);

        if (lmStudioActive === lastLmStudioActive) return;
        lastLmStudioActive = lmStudioActive;

        // Auto select best available engine (on availability change only)
        if (lmStudioActive) {
            currentEngine = 'lm-studio';
            const radioEl = document.querySelector('input[name="llm-engine"][value="lm-studio"]');
            if (radioEl) radioEl.checked = true;
            logSystemEvent("Auto-connected to active LM Studio endpoint");
        } else if (simulationEnabled) {
            currentEngine = 'simulated';
            const radioEl = document.querySelector('input[name="llm-engine"][value="simulated"]');
            if (radioEl) radioEl.checked = true;
            logSystemEvent("No local LLM detected. Falling back to Simulated Claims LLM Mode");
        }
    } catch (error) {
        console.error('Error fetching backend status:', error);
        updateStatusIndicator('lmstudio', false, []);
        if (lastLmStudioActive !== false) {
            lastLmStudioActive = false;
            logSystemEvent('Error connecting to FastAPI backend API', 'error');
        }
    }
}

function updateStatusIndicator(id, isActive, models) {
    const indicator = document.getElementById(`ind-${id}`);
    const label = document.getElementById(`label-${id}`);
    const chip = document.getElementById(`status-${id}`);
    
    if (isActive) {
        indicator.className = 'status-indicator active';
        const modelName = models.length > 0 ? models[0] : 'Ready';
        label.innerText = `LM Studio: ${modelName}`;
        chip.style.borderColor = 'rgba(16, 185, 129, 0.3)';
    } else {
        indicator.className = 'status-indicator error';
        label.innerText = `LM Studio: Offline`;
        chip.style.borderColor = 'rgba(239, 68, 68, 0.15)';
    }
}

// 2. Drag and Drop + File Upload Ingestion
function setupDragAndDrop() {
    ['dragenter', 'dragover'].forEach(eventName => {
        uploadZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            uploadZone.classList.add('dragover');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        uploadZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            uploadZone.classList.remove('dragover');
        }, false);
    });

    uploadZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length > 0) {
            handleFileUpload(files[0]);
        }
    });
}

function setupBrowseButton() {
    browseBtn.addEventListener('click', () => {
        fileInput.click();
    });
    
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files[0]);
        }
    });
}

async function handleFileUpload(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['pdf', 'docx', 'xlsx', 'xls', 'txt'].includes(ext)) {
        alert('Unsupported file format. Please upload PDF, DOCX, Excel, or Text documents.');
        return;
    }

    // Reset upload UI
    uploadProgressContainer.style.display = 'block';
    uploadProgressFill.style.width = '0%';
    uploadProgressStatus.innerText = 'Extracting and parsing text...';
    
    // Simulate UI progress
    let prog = 0;
    const interval = setInterval(() => {
        if (prog < 90) {
            prog += 10;
            uploadProgressFill.style.width = `${prog}%`;
        }
    }, 200);

    const formData = new FormData();
    formData.append('file', file);

    logSystemEvent(`Ingesting file: ${file.name} (${formatBytes(file.size)})`);
    logSystemEvent("Parsing file content and running text extraction pipeline...");

    try {
        const response = await apiFetch('/api/upload', {
            method: 'POST',
            body: formData
        });

        clearInterval(interval);
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Upload failed');
        }

        const result = await response.json();
        
        // Async ingestion mode: the upload was accepted as a job (202). Poll
        // GET /api/jobs/{id} so the progress bar reflects real pipeline state
        // (queued -> parsing -> embedding -> indexed) instead of a simulation.
        if (result.job_id && result.status && result.status !== 'indexed') {
            await pollIngestionJob(result.job_id, file.name);
            setTimeout(() => {
                uploadProgressContainer.style.display = 'none';
            }, 1500);
            await fetchDocuments();
            return;
        }
        
        uploadProgressFill.style.width = '100%';
        uploadProgressStatus.innerText = 'Indexing complete!';
        
        if (result.steps) {
            logSystemEvent(`Document parsed successfully in ${result.steps.parsing_ms}ms`, 'success');
            logSystemEvent(`Chunked, embedded, and stored ${result.chunks_count} parent chunk(s) in SQLite vector store in ${result.steps.db_storage_ms}ms`, 'success');
            logSystemEvent(`Ingestion complete for ${file.name}. Total time: ${result.total_time_ms}ms`, 'success');
        }
        
        setTimeout(() => {
            uploadProgressContainer.style.display = 'none';
        }, 1500);

        await fetchDocuments();
        
    } catch (error) {
        clearInterval(interval);
        uploadProgressContainer.style.display = 'none';
        logSystemEvent(`Ingestion failed for ${file.name}: ${error.message}`, 'error');
        alert(`Failed to ingest document: ${error.message}`);
    }
}

// 3. Guideline Document Management
async function fetchDocuments() {
    try {
        const response = await apiFetch('/api/documents');
        if (!response.ok) throw new Error('Failed to load documents');
        
        documents = await response.json();
        renderDocuments();
    } catch (error) {
        console.error('Error fetching documents:', error);
    }
}

function renderDocuments() {
    documentList.innerHTML = '';
    docCountBadge.innerText = documents.length;
    
    if (documents.length === 0) {
        emptyDocsState.style.display = 'flex';
        return;
    }
    
    emptyDocsState.style.display = 'none';
    
    documents.forEach(doc => {
        const li = document.createElement('li');
        li.className = 'document-item';
        
        let icon = '📄';
        if (doc.file_type === 'pdf') icon = '🟥';
        else if (doc.file_type === 'docx') icon = '🟦';
        else if (['xlsx', 'xls'].includes(doc.file_type)) icon = '🟩';
        else if (doc.file_type === 'txt') icon = '🟨';
        
        li.innerHTML = `
            <div class="doc-info">
                <span class="doc-icon">${icon}</span>
                <div class="doc-meta">
                    <span class="doc-name clickable"></span>
                    <div class="doc-size-date">
                        <span>${formatBytes(doc.file_size)}</span>
                        <span>•</span>
                        <span>${doc.uploaded_at.split(' ')[0]}</span>
                    </div>
                </div>
            </div>
            <button class="btn-delete" title="Delete Guidelines">&times;</button>
        `;

        // Set filename via safe DOM properties (never HTML-parsed) rather than
        // string-interpolating it into innerHTML or an inline onclick -- a
        // malicious filename could otherwise break out of the HTML attribute
        // or the inline event-handler's JS string.
        const nameSpan = li.querySelector('.doc-name');
        nameSpan.textContent = doc.filename;
        nameSpan.title = doc.filename;
        nameSpan.addEventListener('click', () => openDocumentViewer(doc.filename));

        // Delete button logic
        li.querySelector('.btn-delete').addEventListener('click', async (e) => {
            e.stopPropagation();
            if (confirm(`Remove and de-index '${doc.filename}'?`)) {
                await deleteDocument(doc.filename);
            }
        });

        documentList.appendChild(li);
    });
}

async function deleteDocument(filename) {
    logSystemEvent(`Deleting document: ${filename}`);
    try {
        const response = await apiFetch('/api/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename })
        });
        
        if (!response.ok) throw new Error('Delete request failed');
        
        // Async mode: the deletion is a job -- poll it to completion.
        const result = await response.json();
        if (result.job_id && result.status && result.status !== 'deleted') {
            await pollIngestionJob(result.job_id, filename);
        }
        
        logSystemEvent(`Successfully removed and deleted vector chunks for '${filename}'`, 'success');
        await fetchDocuments();
        
        // If DB is empty, let status know
        if (backendStatus) {
            backendStatus.database.document_count = documents.length;
        }
    } catch (error) {
        logSystemEvent(`Failed to delete document: ${error.message}`, 'error');
        alert(`Failed to delete document: ${error.message}`);
    }
}

// 4. Chat and RAG Search Copilot
function setupChatSuggestions() {
    const btns = document.querySelectorAll('.suggested-query-btn');
    btns.forEach(btn => {
        btn.addEventListener('click', () => {
            queryInput.value = btn.getAttribute('data-query');
            queryInput.focus();
        });
    });

    // Enter key submits query (Shift+Enter inserts newline)
    queryInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatForm.requestSubmit();
        }
    });
}

chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const query = queryInput.value.trim();
    if (!query) return;

    queryInput.value = '';

    // Hide welcome card if open
    if (chatWelcome) {
        chatWelcome.style.display = 'none';
    }

    // Append User Message bubble
    addMessageBubble('user', query);

    // Clear log trace
    clearTraceLogs();
    logSystemEvent(`Processing query: "${query}"`);
    logSystemEvent(`Target LLM: ${currentEngine.toUpperCase()}`);

    // Simulated mode has no token stream to relay (the backend collapses it
    // to a single "final" SSE event anyway -- see run_query_stream), so it
    // isn't worth the extra round trip; every other engine streams.
    if (currentEngine === 'simulated') {
        await sendJsonQuery(query);
    } else {
        await sendStreamingQuery(query);
    }
});

if (stopBtn) {
    stopBtn.addEventListener('click', stopStreaming);
}

// Shared by both the JSON and streaming chat paths: logs pipeline_logs to
// the trace timeline and syncs the Prompt Preview tab, exactly as the
// original JSON-only handler did.
function logChatPipeline(query, result) {
    (result.pipeline_logs || []).forEach(log => {
        if (log.includes('Generating query vector') || log.includes('Generated query vector')) {
            logSystemEvent(log, 'system');
        } else if (log.includes('similarity search completed') || log.includes('Vector database similarity search')) {
            logSystemEvent(log, 'system');
        } else if (log.includes('response in') || log.includes('generated response in')) {
            logSystemEvent(log, 'success');
        } else if (log.includes('failed') || log.includes('Error')) {
            logSystemEvent(log, 'error');
        } else {
            logSystemEvent(log);
        }
    });

    updatePromptPreview(query, result.sources || []);
}

// JSON /api/chat path (Phase 4-era, unchanged behavior): used for the
// simulated engine, and as the streaming path's fallback when the SSE
// connection can't be established or fails before any tokens arrive.
async function sendJsonQuery(query) {
    const loadingMessageId = addLoadingBubble();
    try {
        const response = await apiFetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: query,
                engine: currentEngine,
                claim_id: activeCase ? activeCase.id : null
            })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Chat query failed');
        }

        const result = await response.json();

        // Remove loading bubble and append result
        removeLoadingBubble(loadingMessageId);
        addMessageBubble('assistant', result.answer, result.sources, result.engine);
        logChatPipeline(query, result);

    } catch (error) {
        console.error('Error during chat query:', error);
        removeLoadingBubble(loadingMessageId);
        addMessageBubble('assistant', `⚠️ **Error processing query:** ${error.message}. Please verify the backend and chosen LLM server status.`);
        logSystemEvent(`RAG Query pipeline error: ${error.message}`, 'error');
    }
}

// Phase 5.3: streams POST /api/chat/stream (Task 5) and renders token
// deltas into an open bubble as they arrive. Falls back to sendJsonQuery if
// no bytes arrive within 5s (the connection stalled, or e.g. a proxy is
// buffering the SSE response) or if the stream errors before any tokens
// were received -- either way the user still gets an answer, just without
// the incremental render.
async function sendStreamingQuery(query) {
    const loadingMessageId = addLoadingBubble();
    let streamingId = null;
    let accumulated = '';
    let gotBytes = false;
    let finished = false;

    const fallbackTimer = setTimeout(() => {
        if (gotBytes || finished) return;
        finished = true;
        stopStreaming();
        setStreamingActive(false);
        logSystemEvent('No streaming response after 5s; falling back to standard request', 'system');
        removeLoadingBubble(loadingMessageId);
        sendJsonQuery(query);
    }, 5000);

    setStreamingActive(true);

    await streamChat(
        query,
        activeCase ? activeCase.id : null,
        currentEngine,
        (deltaText) => {
            if (finished) return;
            if (!gotBytes) {
                gotBytes = true;
                clearTimeout(fallbackTimer);
                removeLoadingBubble(loadingMessageId);
                streamingId = addStreamingBubble();
            }
            accumulated += deltaText;
            updateStreamingBubble(streamingId, accumulated);
        },
        (result) => {
            if (finished) return;
            finished = true;
            clearTimeout(fallbackTimer);
            setStreamingActive(false);
            if (streamingId) {
                finalizeStreamingBubble(streamingId, result);
            } else {
                // Final arrived with no preceding chunk events (e.g. an
                // immediate refusal with no supporting documents) -- render
                // it the same way the JSON path does.
                removeLoadingBubble(loadingMessageId);
                addMessageBubble('assistant', result.answer, result.sources, result.engine);
            }
            logChatPipeline(query, result);
        },
        (error) => {
            if (finished) return;
            finished = true;
            clearTimeout(fallbackTimer);
            setStreamingActive(false);
            console.error('Error during streaming chat query:', error);
            if (!gotBytes) {
                // Nothing rendered yet -- fall back to the JSON endpoint
                // rather than showing an empty/broken bubble.
                removeLoadingBubble(loadingMessageId);
                logSystemEvent(`Streaming failed (${error.message}); falling back to standard request`, 'error');
                sendJsonQuery(query);
                return;
            }
            if (streamingId) {
                updateStreamingBubble(streamingId, accumulated + `\n\n⚠️ **Stream error:** ${error.message}`);
            }
            logSystemEvent(`RAG Query pipeline error: ${error.message}`, 'error');
        }
    );

    // If nothing above ran (e.g. the user hit Stop: the reader was
    // cancelled, so streamChat's read loop exited normally with neither
    // onFinal nor onError firing), tidy up here.
    if (!finished) {
        finished = true;
        clearTimeout(fallbackTimer);
        setStreamingActive(false);
        removeLoadingBubble(loadingMessageId);
        logSystemEvent('Streaming stopped by user', 'system');
    }
}

// Toggles Stop-button visibility while a stream is in flight.
function setStreamingActive(active) {
    if (stopBtn) stopBtn.style.display = active ? 'inline-block' : 'none';
}

// Creates an empty assistant bubble to render streamed token deltas into,
// mirroring addMessageBubble's markup so the finished bubble (after
// finalizeStreamingBubble runs) looks identical to the non-streaming path.
function addStreamingBubble() {
    const id = 'stream-' + Date.now();
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant';
    msgDiv.id = id;
    msgDiv.innerHTML = `
        <div class="message-label">
            <span>Claims Assistant</span>
        </div>
        <div class="message-bubble"></div>
    `;
    chatMessages.appendChild(msgDiv);
    scrollChatToBottom();
    return id;
}

// Re-renders the bubble's accumulated text through formatMarkdown on every
// chunk -- the same escape-then-format path addMessageBubble uses for the
// JSON path, so streamed LLM output (which quotes retrieved document
// content verbatim) is never assigned to innerHTML unescaped.
function updateStreamingBubble(id, accumulatedText) {
    const msgDiv = document.getElementById(id);
    if (!msgDiv) return;
    msgDiv.querySelector('.message-bubble').innerHTML = formatMarkdown(accumulatedText);
    scrollChatToBottom();
}

// Stamps the engine tag and source citations onto a streaming bubble once
// the final SSE event arrives -- the streaming-path equivalent of what
// addMessageBubble does in one shot for the JSON path. Sources are built as
// real DOM nodes via renderSources() (not string-interpolated), same as
// addMessageBubble.
function finalizeStreamingBubble(id, result) {
    const msgDiv = document.getElementById(id);
    if (!msgDiv) return;

    const bubble = msgDiv.querySelector('.message-bubble');
    bubble.innerHTML = formatMarkdown(result.answer || '');

    if (result.engine) {
        const label = msgDiv.querySelector('.message-label');
        const tag = document.createElement('span');
        tag.className = result.engine === 'simulated' ? 'tag-status badge' : 'tag-status';
        tag.style.marginLeft = '8px';
        tag.style.fontSize = '9px';
        tag.textContent = result.engine.toUpperCase();
        label.appendChild(tag);
    }

    const sourcesEl = renderSources(result.sources);
    if (sourcesEl) bubble.appendChild(sourcesEl);

    scrollChatToBottom();
}

// Streaming counterpart to apiFetch (Phase 5.3): apiFetch's `.then()` never
// reads or locks the response body, so its returned Response is still safe
// to stream from -- reuse it here for the credential attach + 401 handling
// instead of duplicating that logic, and layer on the non-ok -> throw
// behavior streamChat needs before it starts reading the body.
async function apiFetchStream(path, body) {
    const resp = await apiFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp;
}

// Reader for the in-flight chat stream, so stopStreaming() can cancel it.
let chatStreamReader = null;

// Reads the POST /api/chat/stream SSE body (Task 5): calls onChunk(text)
// for each token-delta frame (`data: {"text": "..."}`) and onFinal(event)
// once for the terminal frame carrying the assembled
// answer/sources/engine/pipeline_logs/status -- the same shape the JSON
// /api/chat endpoint returns in one shot. onError fires for network/parse
// failures; a request-level 401/403/429 rejects before any bytes arrive and
// is surfaced there too (see agentic_router.run_query_stream: every
// server-side failure mode, including a mid-stream LLM error, is still
// surfaced as a "final" event, never a bare error frame).
async function streamChat(query, claimId, engine, onChunk, onFinal, onError) {
    let reader;
    try {
        const resp = await apiFetchStream('/api/chat/stream', { query, engine, claim_id: claimId || null });
        reader = resp.body.getReader();
        chatStreamReader = reader;
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split('\n\n');
            buffer = frames.pop();
            for (const frame of frames) {
                const line = frame.split('\n').find(l => l.startsWith('data: '));
                if (!line) continue;
                const data = line.slice(6).trim();
                if (data === '[DONE]') continue;
                const evt = JSON.parse(data);
                if (evt.answer !== undefined) onFinal(evt);
                else if (evt.text) onChunk(evt.text);
            }
        }
    } catch (e) {
        onError(e);
    } finally {
        if (chatStreamReader === reader) chatStreamReader = null;
    }
}

// Bound to the Stop button: cancels the in-flight stream's reader, which
// ends the fetch body read (the for-loop above sees `done` and returns) --
// there is no separate AbortController to manage.
function stopStreaming() {
    if (chatStreamReader) {
        chatStreamReader.cancel().catch(() => {});
    }
}

function addMessageBubble(role, content, sources = [], engine = '') {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    
    const formattedContent = formatMarkdown(content);
    
    let engineTag = '';
    if (role === 'assistant' && engine) {
        let tagClass = 'tag-status';
        if (engine === 'simulated') tagClass = 'tag-status badge';
        engineTag = `<span class="${tagClass}" style="margin-left:8px; font-size:9px;">${engine.toUpperCase()}</span>`;
    }
    
    msgDiv.innerHTML = `
        <div class="message-label">
            <span>${role === 'user' ? 'Claims Handler' : 'Claims Assistant'}</span>
            ${engineTag}
        </div>
        <div class="message-bubble">
            ${formattedContent}
        </div>
    `;

    // Built as real DOM nodes (not string-interpolated) so a malicious
    // filename or document body in a source can't break out of any HTML/JS
    // parsing context -- see renderSources().
    const sourcesEl = renderSources(sources);
    if (sourcesEl) {
        msgDiv.querySelector('.message-bubble').appendChild(sourcesEl);
    }

    chatMessages.appendChild(msgDiv);
    scrollChatToBottom();
}

function addLoadingBubble() {
    const id = 'loading-' + Date.now();
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant';
    msgDiv.id = id;
    msgDiv.innerHTML = `
        <div class="message-label">Claims Assistant</div>
        <div class="message-bubble" style="color: var(--text-muted);">
            <div class="loading-dots">Searching guidelines and thinking...</div>
        </div>
    `;
    chatMessages.appendChild(msgDiv);
    scrollChatToBottom();
    return id;
}

function removeLoadingBubble(id) {
    const element = document.getElementById(id);
    if (element) {
        element.remove();
    }
}

function scrollChatToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// 5. Source Chunk Viewer Modal
function setupModal() {
    modalClose.addEventListener('click', hideModal);
    sourceModal.addEventListener('click', (e) => {
        if (e.target === sourceModal) hideModal();
    });
    // Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && sourceModal.style.display === 'flex') {
            hideModal();
        }
    });
}

let sourceReturnFocus = null;
function showModal(filename, score, content, isImage = false, version = null) {
    sourceReturnFocus = document.activeElement;
    modalFilename.innerText = filename;
    
    if (typeof score === 'number') {
        modalScore.innerText = Number(score).toFixed(3);
        modalViewFullBtn.style.display = 'inline-block';
        modalViewFullBtn.onclick = () => openDocumentViewer(filename, version);
    } else {
        modalScore.innerText = score;
        modalViewFullBtn.style.display = 'none';
    }
    
    if (isImage) {
        modalContent.style.background = '#f0f2f5';
        modalContent.style.fontFamily = 'inherit';
        modalContent.innerHTML = `<div style="text-align: center; padding: 10px 0;"><img src="${content}" style="max-width: 100%; max-height: 440px; border-radius: 4px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); display: inline-block;"></div>`;
    } else {
        modalContent.style.background = '#fafafa';
        modalContent.style.fontFamily = 'monospace';
        modalContent.innerText = content;
    }
    
    sourceModal.style.display = 'flex';
    modalClose.focus();
}

function hideModal() {
    sourceModal.style.display = 'none';
    sourceReturnFocus?.focus();
}

function renderSources(sources) {
    if (!sources || sources.length === 0) return null;

    const wrap = document.createElement('div');

    const title = document.createElement('div');
    title.className = 'sources-title';
    title.textContent = 'Retrieved Reference Citations:';
    wrap.appendChild(title);

    const container = document.createElement('div');
    container.className = 'sources-container';

    sources.forEach(src => {
        let icon = '📄';
        if (src.file_type === 'pdf') icon = '🟥';
        else if (src.file_type === 'docx') icon = '🟦';
        else if (['xlsx', 'xls'].includes(src.file_type)) icon = '🟩';
        else if (src.file_type === 'txt') icon = '🟨';

        const card = document.createElement('div');
        card.className = 'source-card';
        // Calls viewSource with real JS values, not values reconstructed from
        // an HTML/inline-JS string -- a malicious filename or document body
        // can't break out of any parsing context this way.
        card.addEventListener('click', () => viewSource(src.filename, src.score, src.content, src.document_version));

        const fileSpan = document.createElement('span');
        fileSpan.className = 'source-file';
        fileSpan.textContent = `${icon} ${src.filename}`;
        card.appendChild(fileSpan);

        const badge = document.createElement('div');
        badge.className = 'source-score-badge';
        const scoreLabel = document.createElement('span');
        scoreLabel.textContent = 'Ranking score';
        const scoreNum = document.createElement('span');
        scoreNum.className = 'score-num';
        scoreNum.textContent = Number(src.score).toFixed(3);
        badge.appendChild(scoreLabel);
        badge.appendChild(scoreNum);
        card.appendChild(badge);

        container.appendChild(card);
    });

    wrap.appendChild(container);
    return wrap;
}

// Make viewSource globally accessible for the onclick handlers
window.viewSource = function(filename, score, content, version) {
    showModal(filename, score, content, false, version);
};

// 6. RAG Trace Logging
function logSystemEvent(msg, type = 'info') {
    const emptyTrace = traceTimeline.querySelector('.empty-trace-state');
    if (emptyTrace) {
        emptyTrace.remove();
    }
    
    const div = document.createElement('div');
    div.className = `trace-item ${type}`;
    
    const timeStr = new Date().toLocaleTimeString();
    div.innerHTML = `[${timeStr}] ${escapeHtml(msg)}`;
    
    traceTimeline.appendChild(div);
    traceTimeline.scrollTop = traceTimeline.scrollHeight;
}

function clearTraceLogs() {
    traceTimeline.innerHTML = '';
}

function logSystemEventFirstTime() {
    clearTraceLogs();
}

// 7. Prompt Preview Sync
function updatePromptPreview(query, sources) {
    
    const systemPrompt = 
        "You are an expert AI claims handler assistant. Your job is to answer the user's questions about insurance claims, " +
        "policies, or guidelines using ONLY the provided reference documents.\n\n" +
        "Rules:\n" +
        "1. Base your answer strictly on the provided references.\n" +
        "2. If the document content doesn't contain the answer, state that you cannot find it in the guidelines.\n" +
        "3. Provide precise page/section/file references.\n" +
        "4. Keep your answer clear, professional, and well-structured.";
        
    vizSystemPrompt.innerText = systemPrompt;
    
    let contextBlocks = [];
    sources.forEach((src, idx) => {
        contextBlocks.push(`--- SOURCE ${idx+1} | File: ${src.filename} (Sim: ${src.score.toFixed(3)}) ---\n${src.content}\n`);
    });
    
    const userPrompt = 
        `Retrieved Reference Guidelines:\n` +
        `=================================\n` +
        `${contextBlocks.join('\n')}` +
        `=================================\n\n` +
        `Claims Handler Query: ${query}\n\n` +
        `Answer:`;
        
    vizUserPrompt.innerText = userPrompt;
}

// Helper Utilities
function formatBytes(bytes, decimals = 1) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

function escapeHtml(unsafe) {
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

function formatMarkdown(text) {
    // Simple markdown translation
    let html = escapeHtml(text);
    
    // Bold: **text**
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    // Code blocks: ```code```
    html = html.replace(/```(.*?)```/gs, '<pre class="prompt-box" style="margin: 8px 0; background:rgba(0,0,0,0.25); border:1px solid var(--border-color);">$1</pre>');
    
    // Inline code: `code`
    html = html.replace(/`(.*?)`/g, '<code style="font-family:Consolas, monospace; background:rgba(255,255,255,0.1); padding: 1px 4px; border-radius:4px;">$1</code>');
    
    // Bullet points: \n- item
    html = html.replace(/\n-\s+(.*?)/g, '<br>• $1');
    html = html.replace(/\n\*\s+(.*?)/g, '<br>• $1');
    
    // Headings: ### title, ## title, # title
    html = html.replace(/###\s+(.*?)(?=\n|<br>|$)/g, '<h4>$1</h4>');
    html = html.replace(/##\s+(.*?)(?=\n|<br>|$)/g, '<h3>$1</h3>');
    html = html.replace(/#\s+(.*?)(?=\n|<br>|$)/g, '<h2>$1</h2>');
    
    // Newlines to line breaks (unless we just did lists/headings)
    html = html.replace(/\n/g, '<br>');
    
    return html;
}

// =========================================================================
// CLAIMS QUEUE & CASE FOLDER INTEGRATION
// =========================================================================

function setupClaimsCases() {
    // Populate Left Sidebar Cases Queue
    claimsList.innerHTML = '';
    CLAIMS_DATA.forEach(c => {
        const li = document.createElement('li');
        li.className = `claims-list-item ${c.id === activeCase.id ? 'active' : ''}`;
        li.setAttribute('data-id', c.id);

        li.innerHTML = `
            <div class="case-meta">
                <span class="case-id"></span>
                <span class="case-status"></span>
            </div>
            <span class="case-name"></span>
            <span class="case-vehicle"></span>
        `;

        // Claim fields come from a fetched API response (/api/claims), not a
        // string template literal -- set via textContent/classList, never
        // interpolated into innerHTML, so a future claim source that isn't a
        // hardcoded backend constant can't inject markup here (same fix
        // already applied to document filenames elsewhere in this file).
        li.querySelector('.case-id').textContent = c.id;
        const statusEl = li.querySelector('.case-status');
        statusEl.textContent = c.status;
        statusEl.classList.add(c.statusClass);
        li.querySelector('.case-name').textContent = c.insured;
        li.querySelector('.case-vehicle').textContent = c.vehicle;

        li.addEventListener('click', () => {
            document.querySelectorAll('.claims-list-item').forEach(el => el.classList.remove('active'));
            li.classList.add('active');
            
            const caseObj = CLAIMS_DATA.find(x => x.id === c.id);
            if (caseObj) {
                activeCase = caseObj;
                loadCaseFolder(caseObj);
                logSystemEvent(`Loaded claims case folder for ${caseObj.id} (${caseObj.insured})`);
            }
        });
        
        claimsList.appendChild(li);
    });

    // Load initial case
    loadCaseFolder(activeCase);

    // Setup Audit Button Click Listeners
    btnAuditLabor.addEventListener('click', () => triggerAudit('labor'));
    btnAuditOEM.addEventListener('click', () => triggerAudit('oem'));
    btnAuditFraud.addEventListener('click', () => triggerAudit('fraud'));
    btnAuditLetter.addEventListener('click', () => triggerAudit('letter'));
}

function loadCaseFolder(c) {
    // Update text fields
    claimIdText.innerText = c.id;
    claimStatusBadge.innerText = c.status;
    claimStatusBadge.className = `case-status-badge ${c.statusClass}`;
    
    claimInsured.innerText = c.insured;
    claimVehicle.innerText = c.vehicle;
    claimFacility.innerText = c.facility;
    claimTotalEst.innerText = c.totalEst;
    
    claimPolicyPlan.innerText = c.plan;
    claimPolicyDeductible.innerText = c.deductible;
    
    // Update endorsements chips
    claimPolicyEndorsements.innerHTML = '';
    c.endorsements.forEach(e => {
        const span = document.createElement('span');
        span.className = 'endorsement-chip';
        span.innerText = e;
        claimPolicyEndorsements.appendChild(span);
    });
    
    // Update Estimate Table Items. Built as real DOM nodes with textContent
    // (not a string-interpolated innerHTML template) for the same reason as
    // the claim list cards above -- these fields come from a fetched API
    // response, not a trusted literal.
    claimEstimateBody.innerHTML = '';
    c.estimate.forEach(row => {
        const tr = document.createElement('tr');

        const catCell = document.createElement('td');
        const catStrong = document.createElement('strong');
        catStrong.textContent = row.cat;
        catCell.appendChild(catStrong);

        const opCell = document.createElement('td');
        opCell.textContent = row.op;

        const rateCell = document.createElement('td');
        rateCell.textContent = row.rate;

        const qtyCell = document.createElement('td');
        qtyCell.textContent = row.qty;

        const totalCell = document.createElement('td');
        const totalStrong = document.createElement('strong');
        totalStrong.textContent = row.total;
        totalCell.appendChild(totalStrong);

        tr.append(catCell, opCell, rateCell, qtyCell, totalCell);
        claimEstimateBody.appendChild(tr);
    });

    // Fetch attached files for this claim
    fetchClaimDocuments(c.id);
}

function setupTelemetryTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(el => el.classList.remove('active'));
            btn.classList.add('active');
            
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            const targetId = `tab-${btn.getAttribute('data-tab')}`;
            const targetContent = document.getElementById(targetId);
            if (targetContent) {
                targetContent.classList.add('active');
            }
        });
    });
}

async function triggerAudit(type) {
    let queryText = "";
    
    switch (type) {
        case 'labor':
            queryText = `For claim ${activeCase.id} involving vehicle ${activeCase.vehicle} being repaired at ${activeCase.facility}: Check the regional labor rate schedules for 2026. Compare the standard capped rates for Sheet Metal, Frame, Painting, and Mechanical work against the shop charges in our estimate. Highlight any repair lines that exceed the allowable cap limits.`;
            break;
        case 'oem':
            queryText = `Review the OEM Parts policy rider terms. For claim ${activeCase.id} involving a ${activeCase.vehicle} (which is currently under the coverage details specified), does the policy allow the adjuster to write aftermarket or LKQ parts, or does the policyholder's riders mandate brand-new OEM factory-original parts?`;
            break;
        case 'fraud':
            queryText = `Analyze claim ${activeCase.id} for the vehicle ${activeCase.vehicle}. Perform a fraud red flags audit against the claims handler reference directives. Search the database for guidelines regarding pre-existing damage, telematics patterns (braking/speed), and reporting timelines, and list any red flags that adjusters should investigate.`;
            break;
        case 'letter':
            queryText = `Draft a formal, professional Claim Decision and Payout Settlement Letter to the policyholder ${activeCase.insured} for claim ${activeCase.id}. In the letter, explain the vehicle damage assessed (${activeCase.vehicle}), apply the deductible (${activeCase.deductible}), outline covered repairs, list any disallowed charges, and note if subrogation is being pursued against the third party. Quote the corresponding policy sections.`;
            break;
    }
    
    if (!queryText) return;
    
    // Auto-focus chat input, set value, and submit form
    queryInput.value = queryText;
    queryInput.focus();
    
    // Auto switch telemetry tab to trace logs
    document.querySelector('.tab-btn[data-tab="trace"]').click();
    
    // Submit form
    chatForm.requestSubmit();
}

function setupResizableColumns() {
    const resizerLeft = document.getElementById('resizer-left');
    const resizerRight = document.getElementById('resizer-right');
    
    const sidebarLeft = document.querySelector('.sidebar-left');
    const chatRight = document.querySelector('.chat-right');
    const appWorkspace = document.querySelector('.app-workspace');
    
    if (!resizerLeft || !resizerRight) return;
    
    // Left Resizer Dragging
    resizerLeft.addEventListener('mousedown', (e) => {
        e.preventDefault();
        resizerLeft.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        
        function onMouseMove(eMove) {
            const containerRect = appWorkspace.getBoundingClientRect();
            let newWidth = eMove.clientX - containerRect.left;
            
            // Limit bounds
            if (newWidth < 180) newWidth = 180;
            if (newWidth > 380) newWidth = 380;
            
            sidebarLeft.style.flexBasis = `${newWidth}px`;
        }
        
        function onMouseUp() {
            resizerLeft.classList.remove('dragging');
            document.body.style.cursor = '';
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        }
        
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    });
    
    // Right Resizer Dragging
    resizerRight.addEventListener('mousedown', (e) => {
        e.preventDefault();
        resizerRight.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        
        function onMouseMove(eMove) {
            const containerRect = appWorkspace.getBoundingClientRect();
            let newWidth = containerRect.right - eMove.clientX;
            
            // Limit bounds
            if (newWidth < 220) newWidth = 220;
            if (newWidth > 550) newWidth = 550;
            
            chatRight.style.flexBasis = `${newWidth}px`;
        }
        
        function onMouseUp() {
            resizerRight.classList.remove('dragging');
            document.body.style.cursor = '';
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        }
        
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    });
}

// =========================================================================
// CLAIM-SPECIFIC ATTACHMENTS LOGIC
// =========================================================================

async function fetchClaimDocuments(claimId) {
    try {
        const response = await apiFetch(`/api/documents/claim/${encodeURIComponent(claimId)}`);
        if (!response.ok) throw new Error('Failed to load claim documents');
        
        const docs = await response.json();
        renderClaimDocuments(docs);
    } catch (error) {
        console.error('Error fetching claim documents:', error);
    }
}

function renderClaimDocuments(docs) {
    claimDocsList.innerHTML = '';
    
    if (docs.length === 0) {
        claimDocsList.innerHTML = '<li class="empty-claim-docs">No attachments uploaded for this claim.</li>';
        return;
    }
    
    docs.forEach(doc => {
        const li = document.createElement('li');
        li.className = 'claim-docs-item';
        
        let icon = '📄';
        if (doc.file_type === 'pdf') icon = '🟥';
        else if (doc.file_type === 'docx') icon = '🟦';
        else if (['xlsx', 'xls'].includes(doc.file_type)) icon = '🟩';
        else if (doc.file_type === 'txt') icon = '🟨';
        
        li.innerHTML = `
            <div class="doc-info">
                <span>${icon}</span>
                <span class="doc-name clickable"></span>
            </div>
            <div class="doc-actions">
                <button class="btn-delete">🗑️</button>
            </div>
        `;

        // Set filename via safe DOM properties and attach handlers via
        // addEventListener rather than an inline onclick string -- see the
        // same fix in renderDocuments() for why string-interpolating a
        // filename into an inline event handler is unsafe.
        const nameSpan = li.querySelector('.doc-name');
        nameSpan.textContent = doc.filename;
        nameSpan.title = doc.filename;
        nameSpan.addEventListener('click', () => openDocumentViewer(doc.filename));
        li.querySelector('.btn-delete').addEventListener('click', () => deleteClaimDocument(doc.filename));

        claimDocsList.appendChild(li);
    });
}

function setupClaimUpload() {
    // Click triggers file selector
    claimUploadZone.addEventListener('click', () => {
        claimFileInput.click();
    });
    
    claimFileInput.addEventListener('change', async (e) => {
        const files = e.target.files;
        if (!files || files.length === 0) return;
        
        for (let i = 0; i < files.length; i++) {
            await uploadClaimFile(files[i], activeCase.id);
        }
    });
    
    // Drag & Drop
    claimUploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        claimUploadZone.classList.add('dragover');
    });
    
    claimUploadZone.addEventListener('dragleave', () => {
        claimUploadZone.classList.remove('dragover');
    });
    
    claimUploadZone.addEventListener('drop', async (e) => {
        e.preventDefault();
        claimUploadZone.classList.remove('dragover');
        
        const files = e.dataTransfer.files;
        if (!files || files.length === 0) return;
        
        for (let i = 0; i < files.length; i++) {
            await uploadClaimFile(files[i], activeCase.id);
        }
    });
}

async function uploadClaimFile(file, claimId) {
    logSystemEvent(`Attaching file '${file.name}' to claim ${claimId}...`);
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('claim_id', claimId);
    
    try {
        const response = await apiFetch('/api/upload-claim-file', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Upload failed');
        }
        
        const result = await response.json();
        
        // Async ingestion mode: poll the durable job record for real state.
        if (result.job_id && result.status && result.status !== 'indexed') {
            await pollIngestionJob(result.job_id, file.name, claimId);
            await fetchClaimDocuments(claimId);
            return;
        }
        
        const chunkNote = result.chunks_count !== undefined ? ` (Indexed: ${result.chunks_count} chunks)` : '';
        logSystemEvent(`Successfully attached '${file.name}' to dossier${chunkNote}`, 'success');
        
        // Refresh claim attachments
        await fetchClaimDocuments(claimId);
        
    } catch (error) {
        logSystemEvent(`Failed to attach file to dossier: ${error.message}`, 'error');
        alert(`Failed to attach file: ${error.message}`);
    }
}

// Polls GET /api/jobs/{id} until the ingestion job reaches a terminal state,
// driving the upload progress bar from the real job record (Phase 3.2).
async function pollIngestionJob(jobId, filename, claimId = null) {
    const progressByStatus = { queued: 10, parsing: 30, embedding: 70, indexed: 100, deleted: 100 };
    const labelByStatus = {
        queued: 'Queued for async ingestion...',
        parsing: 'Parsing and extracting text...',
        embedding: 'Chunking and embedding...',
        indexed: 'Indexing complete!',
        deleted: 'Delete complete!'
    };
    const scopeNote = claimId ? ` for claim ${claimId}` : '';
    logSystemEvent(`Accepted as job ${jobId} (${filename}${scopeNote}); waiting for completion...`);
    
    while (true) {
        const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (!response.ok) {
            throw new Error(`Job status lookup failed (HTTP ${response.status})`);
        }
        const job = await response.json();
        
        const progress = progressByStatus[job.status] ?? 10;
        uploadProgressFill.style.width = `${progress}%`;
        uploadProgressStatus.innerText = labelByStatus[job.status] || job.status;
        
        if (job.status === 'indexed') {
            uploadProgressFill.style.width = '100%';
            uploadProgressStatus.innerText = 'Indexing complete!';
            logSystemEvent(`Ingestion complete for '${filename}'${scopeNote} (job ${jobId})`, 'success');
            return;
        }
        if (job.status === 'deleted') {
            uploadProgressFill.style.width = '100%';
            uploadProgressStatus.innerText = 'Delete complete!';
            logSystemEvent(`Deletion complete for '${filename}'${scopeNote} (job ${jobId})`, 'success');
            return;
        }
        if (job.status === 'failed') {
            throw new Error(job.error_message || `Job failed (${job.error_code || 'unknown error'})`);
        }
        
        await new Promise(resolve => setTimeout(resolve, 800));
    }
}

// Make deleteClaimDocument globally accessible
window.deleteClaimDocument = async function(filename) {
    logSystemEvent(`Removing attachment: ${filename}`);
    try {
        const response = await apiFetch('/api/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename })
        });
        
        if (!response.ok) throw new Error('Delete request failed');
        
        // Async mode: the deletion is a job -- poll it to completion.
        const result = await response.json();
        if (result.job_id && result.status && result.status !== 'deleted') {
            await pollIngestionJob(result.job_id, filename, activeCase.id);
        }
        
        logSystemEvent(`Removed attachment vector chunks for '${filename}'`, 'success');
        await fetchClaimDocuments(activeCase.id);
    } catch (error) {
        logSystemEvent(`Failed to remove attachment: ${error.message}`, 'error');
        alert(`Failed to delete document: ${error.message}`);
    }
};

async function openDocumentViewer(filename, version = null) {
    const versionQuery = version ? `?version=${encodeURIComponent(version)}` : '';
    const ext = filename.split('.').pop().toLowerCase();
    
    // 1. If it's a PDF or Excel spreadsheet, open it physically in a new tab
    if (ext === 'pdf' || ext === 'xlsx' || ext === 'xls') {
        logSystemEvent(`Opening high-fidelity document in new tab: '${filename}'`);
        // Fetch the bytes with the auth header and open an object URL (a plain
        // window.open navigation cannot carry the Authorization header).
        try {
            const blobUrl = await fetchBlobUrl(`/api/documents/download/${encodeURIComponent(filename)}${versionQuery}`);
            window.open(blobUrl, '_blank');
        } catch (error) {
            logSystemEvent(`Failed to open document: ${error.message}`, 'error');
            alert(`Error opening document: ${error.message}`);
        }
        return;
    }
    
    // 2. If it's an image, render it directly in the modal
    if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext)) {
        logSystemEvent(`Rendering photo in modal viewer: '${filename}'`);
        try {
            const blobUrl = await fetchBlobUrl(`/api/documents/download/${encodeURIComponent(filename)}${versionQuery}`);
            showModal(filename, 'N/A (Image View)', blobUrl, true);
        } catch (error) {
            logSystemEvent(`Failed to render photo: ${error.message}`, 'error');
        }
        return;
    }
    
    // 3. Text or fallback content: fetch text from content API
    logSystemEvent(`Retrieving full text content for document: '${filename}'`);
    try {
        const response = await apiFetch(`/api/documents/content/${encodeURIComponent(filename)}${versionQuery}`);
        if (!response.ok) throw new Error('Failed to load document content');
        
        const result = await response.json();
        showModal(filename, 'N/A (Full Document View)', result.content, false);
    } catch (error) {
        logSystemEvent(`Failed to view document content: ${error.message}`, 'error');
        alert(`Error loading document content: ${error.message}`);
    }
}

window.openDocumentViewer = openDocumentViewer;

// Keep keyboard focus in the visible dialog, including reverse tab navigation.
document.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const modal = ['login-modal', 'source-modal'].map(id => document.getElementById(id))
        .find(element => element && element.style.display === 'flex');
    if (!modal) return;
    const focusable = [...modal.querySelectorAll('button, input, select, [tabindex="0"]')]
        .filter(element => !element.disabled && element.offsetParent !== null);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});
