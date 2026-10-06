const byId = (id) => document.getElementById(id);
const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
const workspaceSelect = byId("workspace-select");
const baseSelect = byId("base-select");
const baseLabel = byId("selected-base-label");
let conversationId = null;
let documents = [];

function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", csrf());
  return fetch(path, { credentials: "same-origin", ...options, headers });
}

function showMessage(target, message, isError = true) {
  target.textContent = message;
  target.dataset.error = isError ? "true" : "false";
}

function showView(name) {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((view) => {
    const active = view.id === `view-${name}`;
    view.classList.toggle("active", active);
    view.hidden = !active;
  });
  if (name === "reviews") loadReviews();
  if (name === "library") loadDocuments();
  if (name === "history") loadHistory();
  if (name === "evaluation") loadEvaluation();
}

async function loadBases() {
  if (!workspaceSelect || !workspaceSelect.value) return;
  const response = await api(`/api/v1/workspaces/${workspaceSelect.value}/bases`);
  if (!response.ok) return;
  const bases = await response.json();
  baseSelect.replaceChildren(new Option("Selecione uma base", ""));
  for (const base of bases) baseSelect.add(new Option(base.name, base.id));
  baseSelect.value = bases[0]?.id || "";
  baseLabel.textContent = bases[0]?.name || "Selecione uma base";
  byId("upload-workspace").value = workspaceSelect.value;
  await loadDocuments();
}

async function loadDocuments() {
  if (!baseSelect?.value) return;
  const response = await api(
    `/api/v1/bases/${baseSelect.value}/documents?workspace_id=${workspaceSelect.value}`,
  );
  if (!response.ok) return;
  documents = await response.json();
  renderDocuments();
}

function renderDocuments() {
  const body = byId("document-rows");
  const filter = (byId("document-filter").value || "").trim().toLowerCase();
  const visible = documents.filter((document) =>
    `${document.title} ${document.format}`.toLowerCase().includes(filter),
  );
  byId("document-total").textContent = `${visible.length} documento${visible.length === 1 ? "" : "s"}`;
  body.replaceChildren();
  if (!visible.length) {
    const row = body.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 6;
    cell.className = "empty-cell";
    cell.textContent = documents.length ? "Nenhum documento corresponde ao filtro." : "Esta base ainda nao tem documentos.";
    return;
  }
  for (const record of visible) {
    const row = body.insertRow();
    const title = row.insertCell();
    const titleText = document.createElement("span");
    titleText.className = "doc-title";
    titleText.textContent = record.title;
    title.append(titleText);
    const subtitle = document.createElement("span");
    subtitle.className = "doc-subtitle";
    subtitle.textContent = `ID ${record.id.slice(0, 8)}`;
    title.append(subtitle);
    row.insertCell().textContent = record.format.toUpperCase();
    row.insertCell().textContent = record.version ? `v${record.version}` : "-";
    const state = row.insertCell();
    const badge = document.createElement("span");
    badge.className = "state-tag";
    badge.dataset.state = record.state;
    badge.textContent = record.state;
    state.append(badge);
    row.insertCell().textContent = record.visibility === "workspace" ? "Workspace" : "Grupos";
    const actions = row.insertCell();
    actions.className = "row-actions";
    const original = document.createElement("button");
    original.type = "button";
    original.className = "source-link";
    original.textContent = "Original";
    original.addEventListener("click", () => {
      location.href = `/api/v1/documents/${record.id}/original?workspace_id=${workspaceSelect.value}`;
    });
    const replace = document.createElement("button");
    replace.type = "button";
    replace.className = "source-link";
    replace.textContent = "Substituir";
    replace.addEventListener("click", () => replaceDocument(record));
    const access = document.createElement("button");
    access.type = "button";
    access.className = "source-link";
    access.textContent = "Acesso";
    access.addEventListener("click", () => changeDocumentAccess(record));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "source-link danger-link";
    remove.textContent = "Excluir";
    remove.addEventListener("click", () => deleteDocument(record));
    actions.append(original, replace, access, remove);
  }
}

async function replaceDocument(record) {
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = ".pdf,.png,.jpg,.jpeg,.md,.markdown,.docx,.xlsx";
  picker.addEventListener("change", async () => {
    if (!picker.files?.length) return;
    const body = new FormData();
    body.set("workspace_id", workspaceSelect.value);
    body.set("upload", picker.files[0]);
    const response = await api(`/api/v1/documents/${record.id}/versions`, { method: "POST", body });
    if (!response.ok) return showMessage(byId("library-message"), "A substituicao foi recusada. Confirme permissao e formato.");
    showMessage(byId("library-message"), "Nova versao enviada; a anterior continua ativa ate a indexacao terminar.", false);
    await loadDocuments();
  });
  picker.click();
}

async function changeDocumentAccess(record) {
  const response = await api(`/api/v1/workspaces/${workspaceSelect.value}/groups`);
  if (!response.ok) return showMessage(byId("library-message"), "Nao foi possivel consultar grupos.");
  const groups = await response.json();
  const names = groups.map((group) => group.name).join(", ");
  const value = prompt(`Digite workspace para acesso geral ou nomes de grupos separados por virgula. Grupos: ${names}`);
  if (value === null) return;
  const publicToWorkspace = value.trim().toLowerCase() === "workspace";
  const selectedNames = value.split(",").map((name) => name.trim().toLowerCase()).filter(Boolean);
  const selected = groups.filter((group) => selectedNames.includes(group.name.toLowerCase()));
  if (!publicToWorkspace && selected.length !== selectedNames.length) {
    return showMessage(byId("library-message"), "Informe apenas grupos existentes.");
  }
  const changed = await api(`/api/v1/documents/${record.id}/access?workspace_id=${workspaceSelect.value}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ public_to_workspace: publicToWorkspace, group_ids: selected.map((group) => group.id) }),
  });
  if (!changed.ok) return showMessage(byId("library-message"), "A politica de acesso nao foi alterada.");
  showMessage(byId("library-message"), "Politica atualizada; consultas e jobs antigos foram invalidados.", false);
  await loadDocuments();
}

async function deleteDocument(record) {
  if (!confirm(`Excluir logicamente ${record.title}? Novos acessos serao bloqueados imediatamente.`)) return;
  const response = await api(`/api/v1/documents/${record.id}?workspace_id=${workspaceSelect.value}`, { method: "DELETE" });
  if (!response.ok) return showMessage(byId("library-message"), "O documento nao foi excluido.");
  showMessage(byId("library-message"), "Documento excluido; a remocao fisica permanece observavel como pendente.", false);
  await loadDocuments();
}

async function createConversation() {
  if (!workspaceSelect.value || !baseSelect.value) throw new Error("Selecione um workspace e uma base.");
  const response = await api("/api/v1/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace_id: workspaceSelect.value, base_id: baseSelect.value }),
  });
  if (!response.ok) throw new Error("Nao foi possivel abrir uma conversa.");
  conversationId = (await response.json()).id;
}

function renderAnswer(result) {
  const conversation = byId("conversation");
  const wrapper = document.createElement("article");
  wrapper.className = "message";
  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = "KnowledgeDesk";
  wrapper.append(label);
  const status = document.createElement("span");
  status.className = "answer-status";
  status.textContent = `${result.mode} / ${result.status}`;
  wrapper.append(status);
  for (const block of result.answer_blocks || []) {
    const paragraph = document.createElement("p");
    paragraph.className = "answer-block";
    paragraph.textContent = block.text;
    wrapper.append(paragraph);
    const citations = document.createElement("div");
    citations.className = "citation-list";
    for (const evidenceId of block.evidence_ids || []) {
      const source = result.sources.find((item) => item.evidence_id === evidenceId);
      if (!source) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "citation-button";
      button.textContent = `${source.title} · v${source.version}`;
      button.addEventListener("click", () => openSource(source));
      citations.append(button);
    }
    wrapper.append(citations);
  }
  if (result.missing_information?.length) {
    const missing = document.createElement("p");
    missing.className = "message-content";
    missing.textContent = result.missing_information.join(" ");
    wrapper.append(missing);
  }
  if (result.clarification_question) {
    const clarification = document.createElement("p");
    clarification.className = "message-content";
    clarification.textContent = result.clarification_question;
    wrapper.append(clarification);
  }
  if (result.mode === "search_only" || result.mode === "demo_simulated") {
    const sources = document.createElement("div");
    sources.className = "citation-list";
    for (const source of result.sources || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "citation-button";
      button.textContent = `${source.title} · v${source.version}`;
      button.addEventListener("click", () => openSource(source));
      sources.append(button);
    }
    wrapper.append(sources);
  }
  conversation.append(wrapper);
  conversation.scrollTop = conversation.scrollHeight;
}

async function openSource(source) {
  const response = await api(source.source_url);
  if (response.status === 404) {
    showMessage(byId("library-message"), "Esta fonte nao esta mais disponivel para sua conta.");
    return;
  }
  if (!response.ok) return;
  const data = await response.json();
  const conversation = byId("conversation");
  const panel = document.createElement("aside");
  panel.className = "source-panel";
  const title = document.createElement("strong");
  title.textContent = `${data.title} · v${data.version}`;
  const excerpt = document.createElement("p");
  excerpt.textContent = data.excerpt;
  const locator = document.createElement("small");
  locator.textContent = JSON.stringify(data.locator);
  panel.append(title, excerpt, locator);
  conversation.append(panel);
  conversation.scrollTop = conversation.scrollHeight;
}

async function loadReviews() {
  if (!workspaceSelect?.value) return;
  byId("review-workspace-label").textContent = "Fila privada deste workspace";
  const response = await api(`/api/v1/reviews?workspace_id=${workspaceSelect.value}`);
  if (response.status === 403) {
    byId("review-list").textContent = "A fila de revisao e visivel apenas para editores e administradores.";
    return;
  }
  if (!response.ok) return;
  const reviews = await response.json();
  byId("review-count").textContent = reviews.filter((item) => item.state === "open").length || "";
  const list = byId("review-list");
  list.replaceChildren();
  if (!reviews.length) {
    list.textContent = "Nenhum encaminhamento aberto.";
    return;
  }
  for (const review of reviews) {
    const item = document.createElement("article");
    item.className = "review-row";
    const details = document.createElement("div");
    const question = document.createElement("strong");
    question.textContent = review.question;
    const meta = document.createElement("p");
    meta.textContent = `${review.reason} · ${review.state}`;
    details.append(question, meta);
    const state = document.createElement("select");
    state.setAttribute("aria-label", "Estado da revisao");
    for (const value of ["in_review", "resolved"]) state.add(new Option(value, value));
    state.value = review.state === "open" ? "in_review" : review.state;
    const update = document.createElement("button");
    update.className = "button button-quiet";
    update.type = "button";
    update.textContent = "Atualizar estado";
    update.addEventListener("click", async () => {
      const response = await api(`/api/v1/reviews/${review.id}?workspace_id=${workspaceSelect.value}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: state.value }),
      });
      if (response.ok) await loadReviews();
    });
    item.append(details, state, update);
    list.append(item);
  }
}

async function loadHistory() {
  if (!workspaceSelect?.value) return;
  const response = await api(`/api/v1/conversations?workspace_id=${workspaceSelect.value}`);
  if (!response.ok) return;
  const conversations = await response.json();
  const list = byId("history-list");
  list.replaceChildren();
  if (!conversations.length) {
    list.textContent = "Nenhuma conversa registrada neste workspace.";
    return;
  }
  for (const conversation of conversations) {
    const item = document.createElement("article");
    item.className = "review-row";
    const details = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `Conversa ${conversation.id.slice(0, 8)}`;
    const date = document.createElement("p");
    date.textContent = new Date(conversation.created_at).toLocaleString("pt-BR");
    details.append(title, date);
    const open = document.createElement("button");
    open.className = "button button-quiet";
    open.type = "button";
    open.textContent = "Abrir";
    open.addEventListener("click", async () => {
      const messages = await api(`/api/v1/conversations/${conversation.id}/messages`);
      if (!messages.ok) return;
      const records = await messages.json();
      details.append(...records.map((record) => {
        const line = document.createElement("p");
        line.textContent = record.content || "Conteudo ocultado porque uma fonte nao esta mais autorizada.";
        return line;
      }));
      open.remove();
    });
    item.append(details, open);
    list.append(item);
  }
}

async function loadEvaluation() {
  const panel = byId("evaluation-panel");
  const response = await api("/api/v1/evaluation/status");
  if (response.status === 403) {
    panel.textContent = "Esta visao e restrita a administradores.";
    return;
  }
  if (!response.ok) {
    panel.textContent = "Nao foi possivel consultar o estado da avaliacao.";
    return;
  }
  const result = await response.json();
  panel.textContent = `Estado: ${result.status}. Modo: ${result.mode}. Dataset: ${result.dataset}. Resultados so aparecem apos execucao real.`;
}

if (byId("login-form")) {
  byId("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const response = await fetch("/api/v1/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    if (response.ok) location.reload();
    else showMessage(byId("login-error"), "Credenciais invalidas ou conta indisponivel.");
  });
}

if (workspaceSelect) {
  workspaceSelect.addEventListener("change", loadBases);
  baseSelect.addEventListener("change", async () => {
    baseLabel.textContent = baseSelect.selectedOptions[0]?.textContent || "Selecione uma base";
    await loadDocuments();
  });
  byId("document-filter").addEventListener("input", renderDocuments);
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  byId("show-upload").addEventListener("click", () => {
    byId("upload-form").hidden = !byId("upload-form").hidden;
  });
  byId("cancel-upload").addEventListener("click", () => {
    byId("upload-form").reset();
    byId("upload-form").hidden = true;
  });
  byId("upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!baseSelect.value) return showMessage(byId("upload-status"), "Selecione uma base.");
    const form = new FormData(event.currentTarget);
    showMessage(byId("upload-status"), "Enviando arquivo...", false);
    const response = await api(`/api/v1/bases/${baseSelect.value}/documents`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) return showMessage(byId("upload-status"), "O arquivo nao foi aceito ou sua permissao nao permite enviar.");
    const job = await response.json();
    showMessage(byId("upload-status"), `Documento recebido. Job ${job.job_id.slice(0, 8)} em processamento.`, false);
    loadDocuments();
    const timer = setInterval(async () => {
      const status = await api(`/api/v1/jobs/${job.job_id}?workspace_id=${workspaceSelect.value}`);
      if (!status.ok) return clearInterval(timer);
      const data = await status.json();
      showMessage(byId("upload-status"), `Processamento: ${data.state}${data.error ? ` (${data.error})` : ""}`, data.state === "failed");
      await loadDocuments();
      if (["ready", "failed"].includes(data.state)) clearInterval(timer);
    }, 2000);
  });
  byId("new-conversation").addEventListener("click", () => {
    conversationId = null;
    byId("conversation").replaceChildren();
  });
  byId("question-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = byId("question-input");
    const question = input.value.trim();
    if (!question) return;
    const conversation = byId("conversation");
    const questionBlock = document.createElement("article");
    questionBlock.className = "message message-user";
    const label = document.createElement("p");
    label.className = "message-label";
    label.textContent = "Voce";
    const text = document.createElement("p");
    text.className = "message-content";
    text.textContent = question;
    questionBlock.append(label, text);
    conversation.append(questionBlock);
    input.value = "";
    const ask = byId("ask-button");
    ask.disabled = true;
    try {
      if (!conversationId) await createConversation();
      const response = await api(`/api/v1/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (!response.ok) throw new Error(response.status === 503 ? "Servico de recuperacao ou geracao indisponivel." : "Nao foi possivel concluir a consulta.");
      const result = await response.json();
      renderAnswer(result);
      const actions = document.createElement("div");
      actions.className = "answer-actions";
      for (const [rating, textLabel] of [["useful", "Util"], ["incorrect", "Incorreta"], ["bad_source", "Fonte inadequada"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = textLabel;
        button.addEventListener("click", () => sendFeedback(result.message_id, rating));
        actions.append(button);
      }
      const review = document.createElement("button");
      review.type = "button";
      review.textContent = "Encaminhar para revisao";
      review.addEventListener("click", () => requestReview(conversationId));
      actions.append(review);
      conversation.append(actions);
    } catch (error) {
      showMessage(byId("library-message"), error.message);
    } finally {
      ask.disabled = false;
    }
  });
  byId("logout-button").addEventListener("click", async () => {
    await api("/api/v1/logout", { method: "POST" });
    location.reload();
  });
  loadBases();
}

async function sendFeedback(messageId, rating) {
  await api(`/api/v1/messages/${messageId}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  });
}

async function requestReview(id) {
  if (!confirm("Enviar esta pergunta e o motivo selecionado para a fila interna de revisao?")) return;
  const response = await api(`/api/v1/conversations/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: true, reason: "answer_review" }),
  });
  if (response.ok) showMessage(byId("library-message"), "Pergunta encaminhada para revisao interna.", false);
}
