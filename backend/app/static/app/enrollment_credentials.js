(() => {
  const status = document.getElementById('copy-status');
  async function copy(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const field = document.createElement('textarea');
        field.value = text;
        field.style.position = 'fixed';
        field.style.opacity = '0';
        document.body.appendChild(field);
        let copied;
        try {
          field.select();
          copied = document.execCommand('copy');
        } finally {
          field.remove();
        }
        if (!copied) throw new Error('Clipboard unavailable');
      }
      status.textContent = 'Copied.';
    } catch {
      status.textContent = 'Could not copy automatically. Select the credentials and copy them manually.';
    }
  }
  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', () => copy(document.getElementById(button.dataset.copy).value));
  });
  document.getElementById('copy-credentials').addEventListener('click', () => {
    copy(`Account ID: ${document.getElementById('account-id').value}\nPassword: ${document.getElementById('account-password').value}`);
  });
})();
