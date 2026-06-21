'use strict';

// ── Config from meta tags (injected by Jinja2) ────────────────────────────────

const MAX_MB = Number(document.querySelector('meta[name="max-upload-mb"]').content) || 15;
const MAX_BYTES = MAX_MB * 1024 * 1024;
const ALLOWED_TYPES = new Set(
  (document.querySelector('meta[name="allowed-types"]').content || 'image/png,image/jpeg,image/webp')
    .split(',').map(s => s.trim()).filter(Boolean)
);

// ── DOM references ────────────────────────────────────────────────────────────

const app          = document.getElementById('app');
const dropZone     = document.getElementById('drop-zone');
const fileInput    = document.getElementById('file-input');
const errorBanner  = document.getElementById('error-banner');
const originalImg  = document.getElementById('original-img');
const resultImg    = document.getElementById('result-img');
const removeBtn    = document.getElementById('remove-btn');
const downloadBtn  = document.getElementById('download-btn');
const tryAnotherBtn = document.getElementById('try-another-btn');
const changeBtn      = document.getElementById('change-btn');
const toleranceSlider  = document.getElementById('tolerance');
const toleranceDisplay = document.getElementById('tolerance-display');
const modelSelect      = document.getElementById('model-select');
const alphaMattingChk  = document.getElementById('alpha-matting');
const mattingParams    = document.getElementById('matting-params');
const fgSlider         = document.getElementById('fg-threshold');
const fgDisplay        = document.getElementById('fg-display');
const bgSlider         = document.getElementById('bg-threshold');
const bgDisplay        = document.getElementById('bg-display');

// ── State ─────────────────────────────────────────────────────────────────────

let currentFile = null;
let resultBlob  = null;

function setState(state) {
  app.dataset.state = state;
}

function showError(msg) {
  errorBanner.textContent = msg;
}

function clearError() {
  errorBanner.textContent = '';
}

// ── Controls: reset to "selected" when any setting changes after a result ─────

function onSettingChange() {
  if (app.dataset.state === 'done') setState('selected');
}

toleranceSlider.addEventListener('input', () => {
  toleranceDisplay.value = toleranceSlider.value;
  onSettingChange();
});

modelSelect.addEventListener('change', onSettingChange);

alphaMattingChk.addEventListener('change', () => {
  mattingParams.classList.toggle('open', alphaMattingChk.checked);
  onSettingChange();
});

fgSlider.addEventListener('input', () => { fgDisplay.value = fgSlider.value; onSettingChange(); });
bgSlider.addEventListener('input', () => { bgDisplay.value = bgSlider.value; onSettingChange(); });

// ── Drop zone ─────────────────────────────────────────────────────────────────

dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    fileInput.click();
  }
});

dropZone.addEventListener('dragover', e => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', e => {
  if (!dropZone.contains(e.relatedTarget)) {
    dropZone.classList.remove('drag-over');
  }
});

dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const files = e.dataTransfer?.files;
  if (files && files.length > 0) selectFile(files[0]);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files.length > 0) selectFile(fileInput.files[0]);
  // Reset so same file can be re-selected after clearing
  fileInput.value = '';
});

// ── File selection ────────────────────────────────────────────────────────────

function selectFile(file) {
  clearError();

  if (!ALLOWED_TYPES.has(file.type)) {
    const ext = file.name.split('.').pop()?.toLowerCase() || '?';
    showError(
      `Unsupported file type (.${ext}). Please upload a PNG, JPEG, or WebP image.`
    );
    return;
  }

  if (file.size > MAX_BYTES) {
    showError(
      `File too large (${(file.size / 1_048_576).toFixed(1)} MB). Maximum is ${MAX_MB} MB.`
    );
    return;
  }

  currentFile = file;
  resultBlob  = null;
  resultImg.src = '';

  // Show original preview
  const previewUrl = URL.createObjectURL(file);
  originalImg.src = previewUrl;
  originalImg.onload = () => URL.revokeObjectURL(previewUrl);

  setState('selected');
}

// ── Process ───────────────────────────────────────────────────────────────────

removeBtn.addEventListener('click', processImage);

async function processImage() {
  if (!currentFile) return;

  clearError();
  setState('processing');

  const formData = new FormData();
  formData.append('file', currentFile);
  formData.append('model', modelSelect.value);
  formData.append('tolerance', toleranceSlider.value);
  formData.append('alpha_matting', alphaMattingChk.checked.toString());
  formData.append('alpha_matting_foreground_threshold', fgSlider.value);
  formData.append('alpha_matting_background_threshold', bgSlider.value);

  try {
    const res = await fetch('/api/remove', {
      method: 'POST',
      body: formData,
    });

    if (!res.ok) {
      let detail = `Server error (${res.status}).`;
      try {
        const json = await res.json();
        detail = json.detail || detail;
      } catch { /* non-JSON body */ }
      throw new Error(detail);
    }

    resultBlob = await res.blob();

    // Display on checkerboard
    const resultUrl = URL.createObjectURL(resultBlob);
    resultImg.src = resultUrl;
    // Don't revoke — the objectURL backs the displayed image

    setState('done');
  } catch (err) {
    showError(err.message || 'An unexpected error occurred. Please try again.');
    setState('selected');
  }
}

// ── Download ──────────────────────────────────────────────────────────────────

downloadBtn.addEventListener('click', () => {
  if (!resultBlob || !currentFile) return;

  const stem = currentFile.name.replace(/\.[^/.]+$/, '');
  const filename = `${stem}-nobg.png`;

  const url = URL.createObjectURL(resultBlob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Delay revoke so the download can start
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// ── Reset ─────────────────────────────────────────────────────────────────────

function resetApp() {
  // Clean up any object URLs still held by the result image
  if (resultImg.src?.startsWith('blob:')) {
    URL.revokeObjectURL(resultImg.src);
  }
  currentFile     = null;
  resultBlob      = null;
  originalImg.src = '';
  resultImg.src   = '';
  clearError();
  setState('initial');
}

tryAnotherBtn.addEventListener('click', resetApp);
changeBtn.addEventListener('click', () => {
  resetApp();
  // Brief timeout so the drop-zone is visible before we open the picker
  setTimeout(() => fileInput.click(), 50);
});

// ── Initial state ─────────────────────────────────────────────────────────────

setState('initial');
