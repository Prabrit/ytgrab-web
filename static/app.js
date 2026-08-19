(() => {
  const urlInput = document.getElementById('urlInput');
  const pullBtn = document.getElementById('pullBtn');
  const modeButtons = document.querySelectorAll('.mode-btn');
  const audioFormat = document.getElementById('audioFormat');
  const videoQuality = document.getElementById('videoQuality');
  const reelLeft = document.getElementById('reelLeft');
  const reelRight = document.getElementById('reelRight');
  const counterPercent = document.getElementById('counterPercent');
  const counterStatus = document.getElementById('counterStatus');
  const counterMeta = document.getElementById('counterMeta');
  const historyList = document.getElementById('historyList');

  let mode = 'audio';
  let polling = null;

  modeButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      mode = btn.dataset.mode;
      modeButtons.forEach((b) => b.classList.toggle('active', b === btn));
      audioFormat.classList.toggle('hidden', mode !== 'audio');
      videoQuality.classList.toggle('hidden', mode !== 'video');
    });
  });

  function setSpinning(on) {
    reelLeft.classList.toggle('spinning', on);
    reelRight.classList.toggle('spinning', on);
  }

  function setStatus(text, cls) {
    counterStatus.textContent = text.toUpperCase();
    counterStatus.className = 'counter-status' + (cls ? ` status-${cls}` : '');
  }

  function resetCounter() {
    counterPercent.textContent = '00.0%';
    counterMeta.textContent = '';
    setStatus('idle', '');
    setSpinning(false);
  }

  function addHistoryItem(job) {
    const empty = historyList.querySelector('.history-empty');
    if (empty) empty.remove();

    const li = document.createElement('li');
    li.className = 'history-item';
    li.dataset.jobId = job.id;
    li.innerHTML = `
      <span class="title">${escapeHtml(job.url)}</span>
      <span class="hstatus">${job.status}</span>
    `;
    historyList.prepend(li);
    return li;
  }

  function updateHistoryItem(li, job) {
    const statusEl = li.querySelector('.hstatus');
    statusEl.textContent = job.status;

    if (job.status === 'done') {
      li.querySelector('.title').textContent = job.title || job.url;
      if (!li.querySelector('.dl-link')) {
        const a = document.createElement('a');
        a.className = 'dl-link';
        a.href = `/api/jobs/${job.id}/file`;
        a.textContent = 'Download';
        li.appendChild(a);
      }
      statusEl.remove();
    } else if (job.status === 'error') {
      statusEl.textContent = 'failed';
      if (job.error && !li.querySelector('.err-text')) {
        const err = document.createElement('span');
        err.className = 'err-text';
        err.title = job.error;
        err.textContent = job.error.length > 90
          ? job.error.slice(0, 90) + '…'
          : job.error;
        li.appendChild(err);
      }
    }
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  async function startJob() {
    const url = urlInput.value.trim();
    if (!url) {
      urlInput.focus();
      return;
    }

    pullBtn.disabled = true;
    resetCounter();
    setStatus('queued', 'queued');
    setSpinning(true);

    const payload = {
      url,
      audio_only: mode === 'audio',
      audio_format: audioFormat.value,
      quality: videoQuality.value,
    };

    let res;
    try {
      res = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      setStatus('network error', 'error');
      setSpinning(false);
      pullBtn.disabled = false;
      return;
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setStatus(body.error || 'error', 'error');
      setSpinning(false);
      pullBtn.disabled = false;
      return;
    }

    const { job_id } = await res.json();
    const historyItem = addHistoryItem({ id: job_id, url, status: 'queued' });
    urlInput.value = '';
    pollJob(job_id, historyItem);
  }

  function pollJob(jobId, historyItem) {
    if (polling) clearInterval(polling);

    polling = setInterval(async () => {
      let res;
      try {
        res = await fetch(`/api/jobs/${jobId}`);
      } catch (err) {
        return; // transient network hiccup, try again next tick
      }
      if (!res.ok) return;
      const job = await res.json();

      counterPercent.textContent = `${job.percent.toFixed(1)}%`;
      if (job.status === 'error') {
        counterMeta.textContent = job.error || 'Unknown error';
      } else {
        counterMeta.textContent = [job.speed, job.eta ? `ETA ${job.eta}` : '']
          .filter(Boolean).join('  ·  ');
      }
      setStatus(job.status, job.status === 'done' ? 'done'
                 : job.status === 'error' ? 'error' : 'downloading');

      if (historyItem) updateHistoryItem(historyItem, job);

      if (job.status === 'done' || job.status === 'error') {
        clearInterval(polling);
        polling = null;
        setSpinning(false);
        pullBtn.disabled = false;
      }
    }, 1000);
  }

  pullBtn.addEventListener('click', startJob);
  urlInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startJob();
  });
})();
