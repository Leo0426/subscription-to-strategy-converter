const DEFAULT_POWERFULLZ = { full: true, fakeip: true, tun: true };

const subUrlEl = document.querySelector("#sub-url");
const resultUrlEl = document.querySelector("#result-url");
const copyBtn = document.querySelector("#copy-btn");
const hintEl = document.querySelector("#simple-hint");

function buildSubscribeUrl(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  try {
    new URL(trimmed);
  } catch {
    return "";
  }
  const url = new URL("/subscribe", window.location.origin);
  url.searchParams.set("subscription_url", trimmed);
  url.searchParams.set("template", "powerfullz");
  url.searchParams.set("target", "mihomo");
  url.searchParams.set("powerfullz", JSON.stringify(DEFAULT_POWERFULLZ));
  return url.toString();
}

function update() {
  const subscribeUrl = buildSubscribeUrl(subUrlEl.value);
  resultUrlEl.value = subscribeUrl;
  copyBtn.disabled = !subscribeUrl;
  if (subUrlEl.value.trim() && !subscribeUrl) {
    hintEl.textContent = "请输入有效的 URL（需包含 http:// 或 https://）";
  } else {
    hintEl.textContent = "";
  }
}

subUrlEl.addEventListener("input", update);

copyBtn.addEventListener("click", async () => {
  if (!resultUrlEl.value) return;
  try {
    await navigator.clipboard.writeText(resultUrlEl.value);
  } catch {
    resultUrlEl.select();
    document.execCommand("copy");
  }
  copyBtn.textContent = "已复制 ✓";
  copyBtn.classList.add("copied");
  setTimeout(() => {
    copyBtn.textContent = "复制";
    copyBtn.classList.remove("copied");
  }, 2000);
});
