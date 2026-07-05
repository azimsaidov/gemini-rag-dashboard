document.addEventListener('DOMContentLoaded', () => {
  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('fileInput');
  const fileList = document.getElementById('fileList');
  const uploadStatus = document.getElementById('uploadStatus');
  const chunkCountBadge = document.getElementById('chunkCountBadge');
  const btnClearSession = document.getElementById('btnClearSession');
  
  const chatLog = document.getElementById('chatLog');
  const promptInput = document.getElementById('promptInput');
  const btnSend = document.getElementById('btnSend');
  const chatStatus = document.getElementById('chatStatus');

  // Load existing session files
  fetchFiles();

  // Drag & Drop Handlers
  ['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
    }, false);
  });

  dropZone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    const files = dt.files;
    if (files.length > 0) {
      uploadFiles(files);
    }
  });

  fileInput.addEventListener('change', (e) => {
    if (fileInput.files.length > 0) {
      uploadFiles(fileInput.files);
    }
  });

  // Upload Function
  async function uploadFiles(files) {
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('files', files[i]);
    }

    uploadStatus.classList.remove('hidden');

    try {
      const response = await fetch('/api/upload', {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        const err = await response.json();
        alert('Upload failed: ' + (err.detail || 'Error uploading files'));
        return;
      }

      await fetchFiles();
    } catch (err) {
      alert('Error connecting to server: ' + err.message);
    } finally {
      uploadStatus.classList.add('hidden');
    }
  }

  // Fetch Session Files
  async function fetchFiles() {
    try {
      const res = await fetch('/api/files');
      const data = await res.json();

      fileList.innerHTML = '';
      if (!data.files || data.files.length === 0) {
        fileList.innerHTML = '<li class="empty-files">No documents uploaded yet</li>';
        chunkCountBadge.textContent = '0 chunks';
      } else {
        data.files.forEach(filename => {
          const li = document.createElement('li');
          li.className = 'file-item';
          li.innerHTML = `📄 <span class="file-item-name">${filename}</span>`;
          fileList.appendChild(li);
        });
        chunkCountBadge.textContent = `${data.total_chunks} chunks`;
      }
    } catch (err) {
      console.error('Error fetching file list:', err);
    }
  }

  // Clear Session
  btnClearSession.addEventListener('click', async () => {
    if (confirm('Clear all uploaded session documents and vector database?')) {
      await fetch('/api/clear', { method: 'POST' });
      await fetchFiles();
      chatLog.innerHTML = `
        <div class="welcome-banner">
          <div class="welcome-icon">🧠</div>
          <h3>Session Cleared</h3>
          <p>Upload new documents to start asking questions!</p>
        </div>
      `;
    }
  });

  // Chat Send Handler
  async function sendQuestion() {
    const question = promptInput.value.trim();
    if (!question) return;

    // Remove welcome banner if present
    const welcome = document.querySelector('.welcome-banner');
    if (welcome) welcome.remove();

    // Append User Message
    appendMessage(question, 'user');
    promptInput.value = '';

    chatStatus.classList.remove('hidden');
    chatLog.scrollTop = chatLog.scrollHeight;

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });

      const data = await res.json();
      if (!res.ok) {
        appendMessage('Error: ' + (data.detail || 'Failed to get response'), 'bot');
      } else {
        appendMessage(data.answer, 'bot', data.citations);
      }
    } catch (err) {
      appendMessage('Error connecting to server: ' + err.message, 'bot');
    } finally {
      chatStatus.classList.add('hidden');
      chatLog.scrollTop = chatLog.scrollHeight;
    }
  }

  btnSend.addEventListener('click', sendQuestion);
  promptInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendQuestion();
    }
  });

  // Append Message UI Helper
  function appendMessage(text, sender, citations = []) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `chat-message ${sender}`;

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';

    if (sender === 'bot') {
      bubble.innerHTML = marked.parse(text);
    } else {
      bubble.textContent = text;
    }

    msgDiv.appendChild(bubble);

    // Citations
    if (citations && citations.length > 0) {
      const citeGroup = document.createElement('div');
      citeGroup.className = 'citations-group';
      citations.forEach(c => {
        const pill = document.createElement('span');
        pill.className = 'citation-pill';
        pill.textContent = `📌 ${c.source} (Chunk #${c.chunk_index})`;
        pill.title = c.snippet;
        citeGroup.appendChild(pill);
      });
      msgDiv.appendChild(citeGroup);
    }

    chatLog.appendChild(msgDiv);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  // Global helper for quick ask chips
  window.quickAsk = function(text) {
    promptInput.value = text;
    sendQuestion();
  };
});
