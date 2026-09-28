"use strict";

// API key và user id chỉ lưu trong localStorage của trình duyệt người dùng.
const STORAGE = { key: "day12.apiKey", user: "day12.userId" };
const USER_ID_PATTERN = /^[A-Za-z0-9_.@-]{1,64}$/;

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"),
  statusText: $("status-text"),
  messages: $("messages"),
  empty: $("empty"),
  composer: $("composer"),
  question: $("question"),
  send: $("send"),
  settings: $("settings"),
  apiKey: $("api-key"),
  toggleKey: $("toggle-key"),
  userId: $("user-id"),
  clear: $("clear"),
  usageMonth: $("usage-month"),
  usageCost: $("usage-cost"),
  usageCostBar: $("usage-cost-bar"),
  usageRate: $("usage-rate"),
  usageRateBar: $("usage-rate-bar"),
  curl: $("curl-example"),
  template: $("message-template"),
};

function storageGet(name) {
  try { return localStorage.getItem(name) || ""; } catch { return ""; }
}

function storageSet(name, value) {
  try { localStorage.setItem(name, value); } catch { /* chế độ ẩn danh: bỏ qua */ }
}

function randomUserId() {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return "web-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function credentials() {
  return { apiKey: els.apiKey.value.trim(), userId: els.userId.value.trim() };
}

function authHeaders() {
  const { apiKey, userId } = credentials();
  return { "X-API-Key": apiKey, "X-User-Id": userId };
}

// Thông báo lỗi dễ hiểu cho từng mã HTTP mà service trả về
function describeError(status, body, headers) {
  switch (status) {
    case 400: return body.detail || "Yêu cầu không hợp lệ.";
    case 401: return "API key sai hoặc chưa nhập — kiểm tra ô API key bên phải.";
    case 402: return "Đã hết ngân sách tháng này cho user này (cost guard).";
    case 422: return "Câu hỏi không hợp lệ (1–2000 ký tự).";
    case 429: {
      const retry = headers.get("Retry-After");
      return `Gửi quá nhanh (rate limit). Thử lại sau ${retry || 60} giây.`;
    }
    case 503: return "Service tạm thời chưa sẵn sàng. Thử lại sau giây lát.";
    default: return `Lỗi ${status}: ${body.detail || "không rõ nguyên nhân"}`;
  }
}

async function api(method, path, body) {
  const options = { method, headers: authHeaders() };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch { /* body rỗng */ }
  return { ok: response.ok, status: response.status, data, headers: response.headers };
}

function addMessage(role, text, meta = "") {
  els.empty.hidden = true;
  const node = els.template.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  node.querySelector(".bubble").textContent = text; // textContent: không bao giờ render HTML
  node.querySelector(".meta").textContent = meta;
  els.messages.append(node);
  els.messages.scrollTop = els.messages.scrollHeight;
  return node;
}

function clearMessages() {
  els.messages.querySelectorAll(".message").forEach((node) => node.remove());
  els.empty.hidden = false;
}

function setMeter(bar, ratio) {
  const clamped = Math.max(0, Math.min(1, ratio));
  bar.style.width = `${(clamped * 100).toFixed(1)}%`;
  bar.classList.toggle("warn", clamped >= 0.7 && clamped < 1);
  bar.classList.toggle("full", clamped >= 1);
}

async function refreshStatus() {
  try {
    const response = await fetch("/ready");
    const ok = response.ok;
    els.status.dataset.state = ok ? "ok" : "down";
    els.statusText.textContent = ok ? "Sẵn sàng" : "Chưa sẵn sàng";
  } catch {
    els.status.dataset.state = "down";
    els.statusText.textContent = "Mất kết nối";
  }
}

async function refreshUsage() {
  if (!credentials().apiKey) return;
  const { ok, data } = await api("GET", "/usage");
  if (!ok) return;
  els.usageMonth.textContent = `· ${data.month}`;
  els.usageCost.textContent = `$${data.spent_usd.toFixed(6)} / $${data.budget_usd}`;
  setMeter(els.usageCostBar, data.spent_usd / data.budget_usd);
  els.usageRate.textContent = `${data.requests_last_minute} / ${data.rate_limit_per_minute}`;
  setMeter(els.usageRateBar, data.requests_last_minute / data.rate_limit_per_minute);
}

async function loadHistory() {
  clearMessages();
  if (!credentials().apiKey) return;
  const { ok, data } = await api("GET", "/history");
  if (!ok) return;
  for (const message of data.messages) {
    addMessage(message.role, message.content);
  }
}

function renderCurl() {
  const { userId } = credentials();
  els.curl.textContent = [
    `curl -X POST ${location.origin}/ask \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H "X-API-Key: $AGENT_API_KEY" \\`,
    `  -H "X-User-Id: ${userId || "sv-01"}" \\`,
    `  -d '{"question":"Docker là gì?"}'`,
  ].join("\n");
}

async function sendQuestion(event) {
  event.preventDefault();
  const question = els.question.value.trim();
  if (!question) return;
  if (!credentials().apiKey) {
    addMessage("error", "Nhập API key ở khung Kết nối trước khi hỏi.");
    els.apiKey.focus();
    return;
  }

  addMessage("user", question);
  els.question.value = "";
  autoResize();
  els.send.disabled = true;
  const pending = addMessage("assistant", "Đang suy nghĩ…");
  pending.classList.add("pending");

  try {
    const { ok, status, data, headers } = await api("POST", "/ask", { question });
    pending.remove();
    if (ok) {
      const replica = headers.get("X-Served-By");
      const meta = [
        `${data.tokens.in}→${data.tokens.out} token`,
        `$${data.cost_usd.toFixed(8)}`,
        `lịch sử ${data.history_length}`,
        replica ? `replica ${replica.slice(0, 12)}` : "",
      ].filter(Boolean).join(" · ");
      addMessage("assistant", data.answer, meta);
    } else {
      addMessage("error", describeError(status, data, headers));
    }
  } catch {
    pending.remove();
    addMessage("error", "Không gọi được service — kiểm tra kết nối mạng.");
  } finally {
    els.send.disabled = false;
    els.question.focus();
    refreshUsage();
  }
}

function saveSettings(event) {
  event.preventDefault();
  if (!USER_ID_PATTERN.test(els.userId.value.trim())) {
    els.userId.reportValidity();
    return;
  }
  storageSet(STORAGE.key, els.apiKey.value.trim());
  storageSet(STORAGE.user, els.userId.value.trim());
  renderCurl();
  loadHistory();
  refreshUsage();
}

async function clearHistory() {
  if (!credentials().apiKey) return;
  const { ok } = await api("DELETE", "/history");
  if (ok) clearMessages();
}

function autoResize() {
  els.question.style.height = "auto";
  els.question.style.height = `${Math.min(els.question.scrollHeight, 160)}px`;
}

function init() {
  els.apiKey.value = storageGet(STORAGE.key);
  els.userId.value = storageGet(STORAGE.user) || randomUserId();
  storageSet(STORAGE.user, els.userId.value);

  els.composer.addEventListener("submit", sendQuestion);
  els.settings.addEventListener("submit", saveSettings);
  els.clear.addEventListener("click", clearHistory);
  els.toggleKey.addEventListener("click", () => {
    els.apiKey.type = els.apiKey.type === "password" ? "text" : "password";
  });
  els.question.addEventListener("input", autoResize);
  els.question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      els.composer.requestSubmit();
    }
  });

  renderCurl();
  refreshStatus();
  loadHistory();
  refreshUsage();
  setInterval(refreshStatus, 15000);
}

init();
