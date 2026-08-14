async function fetchInfo() {
    const url = document.getElementById('urlInput').value.trim();
    const loader = document.getElementById('loader');
    const resultDiv = document.getElementById('result');
    const errorDiv = document.getElementById('error');
    const formatList = document.getElementById('formatList');
    
    if (!url) return;

    loader.classList.remove('hidden');
    resultDiv.classList.add('hidden');
    errorDiv.classList.add('hidden');
    formatList.innerHTML = '';

    try {
        const response = await fetch('/api/info', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || 'Failed to fetch video information.');
        }

        document.getElementById('videoTitle').textContent = data.title;
        document.getElementById('videoThumb').src = data.thumbnail;
        
        if (data.formats.length === 0) {
            throw new Error("No downloadable formats available.");
        }

        data.formats.forEach(f => {
            const btn = document.createElement('a');
            btn.href = f.url;
            btn.target = '_blank';
            btn.className = 'format-btn';
            
            const size = f.filesize ? (f.filesize / (1024*1024)).toFixed(1) + ' MB' : 'Size Unknown';
            btn.textContent = `⬇ ${f.ext.toUpperCase()} | ${f.resolution} | ${size}`;
            formatList.appendChild(btn);
        });

        resultDiv.classList.remove('hidden');
    } catch (err) {
        errorDiv.textContent = `Error: ${err.message}`;
        errorDiv.classList.remove('hidden');
    } finally {
        loader.classList.add('hidden');
    }
}

document.getElementById('urlInput')?.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') fetchInfo();
});