let currentRows = [];
let activeField = null;

document.addEventListener('focusin', (e) => {
  if (e.target.matches('input,textarea')) activeField = e.target;
});

document.querySelectorAll('.dia').forEach((b) => {
  b.addEventListener('click', () => {
    if (!activeField) return;
    const ch = b.textContent;
    const s = activeField.selectionStart ?? activeField.value.length;
    const e = activeField.selectionEnd ?? activeField.value.length;
    activeField.value = activeField.value.slice(0, s) + ch + activeField.value.slice(e);
    activeField.selectionStart = activeField.selectionEnd = s + 1;
    activeField.dispatchEvent(new Event('input'));
  });
});

function renderRows() {
  const tbody = document.querySelector('#rows tbody');
  tbody.innerHTML = '';
  currentRows.forEach((row, idx) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><input data-i="${idx}" data-k="solution" value="${row.solution || ''}" /></td>
                    <td><textarea data-i="${idx}" data-k="definition">${row.definition || ''}</textarea></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll('input,textarea').forEach((el) => {
    el.addEventListener('input', (e) => {
      const i = Number(e.target.dataset.i);
      const k = e.target.dataset.k;
      currentRows[i][k] = e.target.value;
    });
  });
}

document.getElementById('extract').addEventListener('click', async () => {
  const title = document.getElementById('title').value.trim();
  const clue = document.getElementById('clue').files[0];
  const solution = document.getElementById('solution').files[0];
  if (!title || !clue || !solution) return;
  const form = new FormData();
  form.append('puzzle_title', title);
  form.append('clue_image', clue);
  form.append('solution_image', solution);
  const res = await fetch('/api/extract', { method: 'POST', body: form });
  const data = await res.json();
  currentRows = data.rows;
  renderRows();
});

document.getElementById('save').addEventListener('click', async () => {
  const title = document.getElementById('title').value.trim();
  const rows = currentRows.map((r) => ({ puzzle_title: title, solution: r.solution || '', definition: r.definition || '' }));
  await fetch('/api/save-jsonl', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ puzzle_title: title, rows }),
  });
  alert('Saved');
});


document.getElementById('merge').addEventListener('click', async () => {
  await fetch('/api/merge-jsonl', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  alert('Merged');
});
