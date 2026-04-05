chrome.storage.local.get('lastRun', ({ lastRun }) => {
  document.getElementById('lastRun').textContent =
    lastRun ? `Last run: ${new Date(lastRun).toLocaleString('en-AU')}` : 'Last run: never';
});

document.getElementById('runNow').addEventListener('click', async () => {
  const btn    = document.getElementById('runNow');
  const status = document.getElementById('status');
  btn.disabled = true;
  btn.textContent = 'Scraping...';
  status.textContent = '';

  try {
    // Send message to background to run immediately
    await chrome.runtime.sendMessage({ type: 'scrapeNow' });
    status.textContent = 'Scrape triggered — check VPS dashboard in ~30s.';
  } catch (err) {
    status.textContent = 'Error: ' + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Scrape Now';
  }
});
