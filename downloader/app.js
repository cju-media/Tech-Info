const menuBtn = document.getElementById('menu-btn');
const apiKeyMenu = document.getElementById('api-key-menu');
const apiKeyInput = document.getElementById('api-key-input');
const statusDiv = document.getElementById('status');
const folderUrlInput = document.getElementById('folder-url-input');
const loadFolderBtn = document.getElementById('load-folder-btn');
const fileList = document.getElementById('file-list');
const breadcrumbsDiv = document.getElementById('breadcrumbs');
const selectAllCheckbox = document.getElementById('select-all-checkbox');
const cartList = document.getElementById('cart-list');
const cartCountSpan = document.getElementById('cart-count');
const checkoutBtn = document.getElementById('checkout-btn');
const progressContainer = document.getElementById('download-progress');
const progressBarFill = document.getElementById('progress-bar-fill');
const progressText = document.getElementById('progress-text');

let apiKey = localStorage.getItem('GDRIVE_API_KEY') || '';
if (apiKey) apiKeyInput.value = apiKey;

// State
let currentFolderId = null;
let currentPath = []; // Array of { id, name }
let currentItems = []; // Items in the current folder
let cart = new Map(); // id -> item

// UI Events
menuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    apiKeyMenu.classList.toggle('show');
});

document.addEventListener('click', (e) => {
    if (!apiKeyMenu.contains(e.target) && e.target !== menuBtn) {
        apiKeyMenu.classList.remove('show');
    }
});

function saveApiKey() {
    apiKey = apiKeyInput.value.trim();
    if (apiKey) {
        localStorage.setItem('GDRIVE_API_KEY', apiKey);
        setStatus('API Key saved!', 'success');
        apiKeyMenu.classList.remove('show');
    } else {
        localStorage.removeItem('GDRIVE_API_KEY');
        setStatus('API Key removed.', 'info');
        apiKeyMenu.classList.remove('show');
    }
}

function setStatus(msg, type) {
    statusDiv.textContent = msg;
    statusDiv.className = type;
}

function extractFolderId(url) {
    const match = url.match(/folders\/([a-zA-Z0-9_-]+)/);
    if (match) return match[1];
    const idMatch = url.match(/id=([a-zA-Z0-9_-]+)/);
    return idMatch ? idMatch[1] : null;
}

loadFolderBtn.addEventListener('click', () => {
    if (!apiKey) {
        setStatus('Please configure your Google API Key first.', 'error');
        return;
    }
    const url = folderUrlInput.value.trim();
    const folderId = extractFolderId(url);
    if (!folderId) {
        setStatus('Invalid Google Drive Folder URL', 'error');
        return;
    }

    // Clear state
    cart.clear();
    updateCartUI();
    currentPath = [];

    // Fetch root folder details first to get its name
    fetchFolderDetails(folderId).then(folderName => {
        currentPath.push({ id: folderId, name: folderName || 'Root' });
        loadFolderContents(folderId);
    }).catch(err => {
        console.error(err);
        setStatus('Error loading folder. Check API key and folder permissions.', 'error');
    });
});

async function fetchFolderDetails(folderId) {
    const url = `https://www.googleapis.com/drive/v3/files/${folderId}?key=${apiKey}&fields=name`;
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to fetch folder details');
    const data = await res.json();
    return data.name;
}

async function loadFolderContents(folderId) {
    setStatus('Loading...', 'info');
    fileList.innerHTML = '';
    currentItems = [];
    selectAllCheckbox.checked = false;
    renderBreadcrumbs();

    try {
        const url = `https://www.googleapis.com/drive/v3/files?q='${folderId}'+in+parents+and+trashed=false&fields=files(id,name,mimeType,size)&orderBy=folder,name&key=${apiKey}`;
        const res = await fetch(url);
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error.message || 'Failed to fetch contents');
        }
        const data = await res.json();
        currentItems = data.files || [];

        if (currentItems.length === 0) {
            fileList.innerHTML = '<li class="empty-msg">Folder is empty</li>';
        } else {
            renderFileList();
        }
        setStatus('', 'info');
    } catch (err) {
        console.error(err);
        setStatus(`Error: ${err.message}`, 'error');
    }
}

function renderBreadcrumbs() {
    breadcrumbsDiv.innerHTML = '';
    currentPath.forEach((node, index) => {
        const span = document.createElement('span');
        span.textContent = node.name;
        if (index < currentPath.length - 1) {
            span.className = 'breadcrumb-link';
            span.onclick = () => {
                // Navigate back
                currentPath = currentPath.slice(0, index + 1);
                loadFolderContents(node.id);
            };
            breadcrumbsDiv.appendChild(span);

            const sep = document.createElement('span');
            sep.textContent = ' > ';
            sep.className = 'breadcrumb-separator';
            breadcrumbsDiv.appendChild(sep);
        } else {
            breadcrumbsDiv.appendChild(span);
        }
    });
}

function renderFileList() {
    fileList.innerHTML = '';
    currentItems.forEach(item => {
        const isFolder = item.mimeType === 'application/vnd.google-apps.folder';

        const li = document.createElement('li');
        li.className = 'file-item';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'item-checkbox';
        checkbox.checked = cart.has(item.id);
        checkbox.onchange = () => toggleCart(item, checkbox.checked);

        const icon = document.createElement('span');
        icon.className = 'item-icon';
        icon.textContent = isFolder ? '📁' : '📄';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'item-name' + (isFolder ? ' folder-link' : '');
        nameSpan.textContent = item.name;
        if (isFolder) {
            nameSpan.onclick = () => {
                currentPath.push({ id: item.id, name: item.name });
                loadFolderContents(item.id);
            };
        }

        li.appendChild(checkbox);
        li.appendChild(icon);
        li.appendChild(nameSpan);

        if (!isFolder && item.size) {
            const sizeSpan = document.createElement('span');
            sizeSpan.className = 'item-size';
            sizeSpan.textContent = formatBytes(item.size);
            li.appendChild(sizeSpan);
        }

        fileList.appendChild(li);
    });

    updateSelectAllState();
}

selectAllCheckbox.addEventListener('change', (e) => {
    const isChecked = e.target.checked;
    const checkboxes = document.querySelectorAll('.item-checkbox');
    checkboxes.forEach((cb, index) => {
        cb.checked = isChecked;
        toggleCart(currentItems[index], isChecked);
    });
});

function updateSelectAllState() {
    if (currentItems.length === 0) {
        selectAllCheckbox.checked = false;
        return;
    }
    const allChecked = currentItems.every(item => cart.has(item.id));
    selectAllCheckbox.checked = allChecked;
}

function toggleCart(item, isAdded) {
    if (isAdded) {
        cart.set(item.id, item);
    } else {
        cart.delete(item.id);
    }
    updateCartUI();
    updateSelectAllState();
}

function updateCartUI() {
    cartList.innerHTML = '';
    let count = 0;
    cart.forEach(item => {
        count++;
        const li = document.createElement('li');
        li.className = 'cart-item';

        const name = document.createElement('span');
        name.textContent = item.name;

        const removeBtn = document.createElement('button');
        removeBtn.textContent = '❌';
        removeBtn.className = 'remove-btn';
        removeBtn.onclick = () => {
            cart.delete(item.id);
            updateCartUI();
            // Also uncheck in main list if visible
            const itemIndex = currentItems.findIndex(i => i.id === item.id);
            if (itemIndex !== -1) {
                const checkboxes = document.querySelectorAll('.item-checkbox');
                if (checkboxes[itemIndex]) checkboxes[itemIndex].checked = false;
                updateSelectAllState();
            }
        };

        li.appendChild(name);
        li.appendChild(removeBtn);
        cartList.appendChild(li);
    });

    cartCountSpan.textContent = count;
    checkoutBtn.disabled = count === 0;
}

// Format bytes to human readable
function formatBytes(bytes, decimals = 2) {
    if (!+bytes) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

// ---------------- Downloading Logic ----------------

checkoutBtn.addEventListener('click', async () => {
    if (cart.size === 0) return;

    checkoutBtn.disabled = true;
    progressContainer.style.display = 'block';
    updateProgress(0, 'Preparing download...');

    try {
        const zip = new JSZip();
        let totalFilesToDownload = 0;
        let downloadedFilesCount = 0;

        // Helper to recursively count and fetch files
        async function processItem(item, currentFolder) {
            if (item.mimeType === 'application/vnd.google-apps.folder') {
                const subFolder = currentFolder.folder(item.name);
                const subItems = await fetchAllContents(item.id);
                for (const subItem of subItems) {
                    await processItem(subItem, subFolder);
                }
            } else {
                totalFilesToDownload++;
                // Determine if it's a Google Workspace document
                if (item.mimeType.startsWith('application/vnd.google-apps.')) {
                    // Export Google docs as pdf/docx etc (Skipping or simple export)
                    console.log(`Skipping Google Workspace document for zip: ${item.name}`);
                    totalFilesToDownload--;
                    return;
                }

                updateProgress(Math.floor((downloadedFilesCount / Math.max(totalFilesToDownload, 1)) * 50), `Fetching ${item.name}...`);
                const blob = await downloadFile(item.id);
                currentFolder.file(item.name, blob);
                downloadedFilesCount++;
                updateProgress(Math.floor((downloadedFilesCount / totalFilesToDownload) * 50), `Fetched ${item.name}`);
            }
        }

        const itemsToProcess = Array.from(cart.values());

        // First pass: just count files to give better progress? Or do it on the fly.
        // Doing on the fly for simplicity.

        for (const item of itemsToProcess) {
             await processItem(item, zip);
        }

        updateProgress(50, 'Zipping files...');

        const zipBlob = await zip.generateAsync({ type: 'blob' }, (metadata) => {
            const percent = 50 + (metadata.percent / 2); // Map 0-100 to 50-100
            updateProgress(percent, `Zipping... ${Math.floor(percent)}%`);
        });

        saveAs(zipBlob, `drive_download_${Date.now()}.zip`);

        updateProgress(100, 'Download complete!');
        setTimeout(() => {
            progressContainer.style.display = 'none';
        }, 3000);

    } catch (err) {
        console.error(err);
        setStatus(`Download error: ${err.message}`, 'error');
        updateProgress(0, 'Error occurred');
    } finally {
        checkoutBtn.disabled = cart.size === 0;
    }
});

async function fetchAllContents(folderId) {
    let allFiles = [];
    let pageToken = null;

    do {
        let url = `https://www.googleapis.com/drive/v3/files?q='${folderId}'+in+parents+and+trashed=false&fields=nextPageToken,files(id,name,mimeType)&key=${apiKey}`;
        if (pageToken) {
            url += `&pageToken=${pageToken}`;
        }

        const res = await fetch(url);
        if (!res.ok) throw new Error('Failed to fetch subfolder contents');
        const data = await res.json();

        allFiles = allFiles.concat(data.files || []);
        pageToken = data.nextPageToken;
    } while (pageToken);

    return allFiles;
}

async function downloadFile(fileId) {
    const url = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media&key=${apiKey}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to download file ${fileId}`);
    return await res.blob();
}

function updateProgress(percent, text) {
    progressBarFill.style.width = `${percent}%`;
    progressText.textContent = text;
}
