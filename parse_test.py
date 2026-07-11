import re

with open("index.html", "r") as f:
    content = f.read()

# First we need to fetch public_events.json when index.html loads
fetch_logic = """        let allEvents = [];
        let publicEvents = [];

        // Fetch worship scripts mapping"""
content = content.replace("        let allEvents = [];\n\n        // Fetch worship scripts mapping", fetch_logic)

fetch_public_events = """
        // Fetch Public Events (for RF coordination overlap checking)
        fetch('public_events.json')
            .then(response => response.json())
            .then(data => {
                publicEvents = data;
            })
            .catch(error => console.error('Error fetching public events:', error));
"""
content = content.replace("        // Fetch worship scripts mapping", fetch_public_events + "\n        // Fetch worship scripts mapping")

# Then in openModal we add logic to check if there is an overlapping public event
modal_overlap_logic = """
            // Check for overlapping public events
            const publicEventNote = document.getElementById('event-modal-public-note');
            if (publicEventNote) publicEventNote.remove();

            if (publicEvents && publicEvents.length > 0 && event.dateObj) {
                const year = event.dateObj.getFullYear();
                const month = String(event.dateObj.getMonth() + 1).padStart(2, '0');
                const day = String(event.dateObj.getDate()).padStart(2, '0');
                const dateKey = `${year}-${month}-${day}`;

                // Find if any public event is on the same day
                const overlapping = publicEvents.filter(pe => pe.date === dateKey || pe.date.startsWith(dateKey));
                if (overlapping.length > 0) {
                    const note = document.createElement('div');
                    note.id = 'event-modal-public-note';
                    note.style.marginTop = '15px';
                    note.style.padding = '10px';
                    note.style.backgroundColor = '#fff3cd';
                    note.style.color = '#856404';
                    note.style.border = '1px solid #ffeeba';
                    note.style.borderRadius = '5px';
                    note.style.fontSize = '14px';
                    note.style.textAlign = 'center';

                    let conflictText = '<strong>⚠️ Nearby Public Event Conflict:</strong><br>';
                    overlapping.forEach(pe => {
                        conflictText += `<em>${pe.name}</em> at ${pe.location}<br>`;
                    });
                    note.innerHTML = conflictText;

                    buttonContainer.appendChild(note);
                }
            }
"""

content = content.replace("                 // We don't want it exactly at appContainer half", modal_overlap_logic + "\n                 // We don't want it exactly at appContainer half")

with open("index.html", "w") as f:
    f.write(content)
