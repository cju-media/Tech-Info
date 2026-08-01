const searchInput = document.getElementById('search-input');
const statusDiv = document.getElementById('status');
const tbody = document.getElementById('events-tbody');
const headers = document.querySelectorAll('th[data-sort]');

let events = [];
let sortCol = 'createdTime';
let sortDesc = true; // Default sort: newest first

// Format ISO date to local readable string
function formatDate(isoString) {
    if (!isoString) return 'Unknown';
    const d = new Date(isoString);
    if (isNaN(d)) return isoString;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

async function loadEvents() {
    try {
        const res = await fetch(`events_data.json?t=${Date.now()}`);
        if (!res.ok) throw new Error('Could not fetch events directory');
        events = await res.json();

        statusDiv.style.display = 'none';
        renderTable();
    } catch (err) {
        console.error(err);
        statusDiv.textContent = 'Error loading events. Please try again later.';
        statusDiv.style.color = '#e74c3c';
    }
}

function renderTable() {
    const query = searchInput.value.toLowerCase().trim();

    // Filter
    let filtered = events.filter(e => {
        const nameMatch = (e.name || '').toLowerCase().includes(query);
        const dateMatch = formatDate(e.createdTime).toLowerCase().includes(query);
        return nameMatch || dateMatch;
    });

    // Sort
    filtered.sort((a, b) => {
        let valA = a[sortCol] || '';
        let valB = b[sortCol] || '';

        if (sortCol === 'name') {
            valA = valA.toLowerCase();
            valB = valB.toLowerCase();
        }

        if (valA < valB) return sortDesc ? 1 : -1;
        if (valA > valB) return sortDesc ? -1 : 1;
        return 0;
    });

    // Render
    tbody.innerHTML = '';

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="2" style="text-align: center; color: #888;">No events found</td></tr>';
        return;
    }

    filtered.forEach(item => {
        const tr = document.createElement('tr');
        tr.onclick = () => {
            window.location.href = `../downloader/index.html?folderId=${encodeURIComponent(item.id)}`;
        };

        // Accessibility: allow pressing Enter on row to activate
        tr.tabIndex = 0;
        tr.onkeydown = (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                tr.click();
            }
        };

        const tdDate = document.createElement('td');
        tdDate.className = 'date-col';
        tdDate.textContent = formatDate(item.createdTime);

        const tdName = document.createElement('td');
        tdName.className = 'name-col';
        tdName.textContent = item.name;

        tr.appendChild(tdDate);
        tr.appendChild(tdName);
        tbody.appendChild(tr);
    });

    updateHeaderIcons();
}

function updateHeaderIcons() {
    headers.forEach(th => {
        if (th.dataset.sort === sortCol) {
            th.setAttribute('aria-sort', sortDesc ? 'descending' : 'ascending');
        } else {
            th.setAttribute('aria-sort', 'none');
        }
    });
}

// Event Listeners
searchInput.addEventListener('input', renderTable);

headers.forEach(th => {
    const handleSort = () => {
        const col = th.dataset.sort;
        if (sortCol === col) {
            sortDesc = !sortDesc; // Toggle direction
        } else {
            sortCol = col;
            sortDesc = col === 'createdTime'; // Default createdTime to desc, name to asc
        }
        renderTable();
    };

    th.addEventListener('click', handleSort);
    th.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleSort();
        }
    });
});

// Init
loadEvents();