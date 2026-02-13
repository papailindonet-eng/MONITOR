const API_STATUS = '/api/status';
const API_ACK = '/api/ack';

const elements = {
  syncStatus: document.getElementById('sync-status'),
  lastUpdate: document.getElementById('last-update'),
  totalFila: document.getElementById('total-fila'),
  seisCount: document.getElementById('seis-count'),
  sourceUrl: document.getElementById('source-url'),
  vehicleCount: document.getElementById('vehicle-count'),
  vehiclesBody: document.getElementById('vehicles-body'),
  alertOverlay: document.getElementById('alert-overlay'),
  alertList: document.getElementById('alert-list'),
  confirmAlert: document.getElementById('confirm-alert'),
};

const alarm = {
  audioContext: null,
  oscillators: [],
  gainNode: null,
  activeEventId: 0,
};

function formatTimestamp(iso) {
  if (!iso) return '--';
  const date = new Date(iso);
  return date.toLocaleString('pt-BR');
}

function clearTable() {
  elements.vehiclesBody.innerHTML = '';
}

function createStatusChip(status) {
  const span = document.createElement('span');
  span.className = 'status-chip';
  span.textContent = status || '--';
  if ((status || '').toUpperCase() === 'CHAMADO DA PORTARIA') {
    span.classList.add('alert');
  }
  return span;
}

function renderVehicles(vehicles) {
  clearTable();
  vehicles.forEach((vehicle) => {
    const row = document.createElement('tr');
    if ((vehicle.transportadora || '').toUpperCase() === 'TRANSPORTADORA SEIS') {
      row.classList.add('row-highlight');
    }
    row.innerHTML = `
      <td>${vehicle.agenda || '--'}</td>
      <td>${vehicle.caminhao || '--'}</td>
      <td>${vehicle.transportadora || '--'}</td>
      <td>${vehicle.pdt || '--'}</td>
      <td></td>
      <td>${vehicle.local || '--'}</td>
      <td>${vehicle.previsao || '--'}</td>
    `;
    row.children[4].appendChild(createStatusChip(vehicle.status));
    elements.vehiclesBody.appendChild(row);
  });
}

function updateAlertList(alertVehicles) {
  elements.alertList.innerHTML = '';
  alertVehicles.forEach((vehicle) => {
    const li = document.createElement('li');
    li.textContent = `${vehicle.agenda} - ${vehicle.caminhao} (${vehicle.transportadora})`;
    elements.alertList.appendChild(li);
  });
}

function startAlarm() {
  if (alarm.audioContext) return;
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;
  const context = new AudioContext();
  const gainNode = context.createGain();
  gainNode.gain.value = 0.8;
  gainNode.connect(context.destination);

  const frequencies = [880, 1320, 1760];
  const oscillators = frequencies.map((freq) => {
    const osc = context.createOscillator();
    osc.type = 'square';
    osc.frequency.value = freq;
    osc.connect(gainNode);
    osc.start();
    return osc;
  });

  alarm.audioContext = context;
  alarm.oscillators = oscillators;
  alarm.gainNode = gainNode;

  if (context.state === 'suspended') {
    context.resume();
  }
}

function stopAlarm() {
  alarm.oscillators.forEach((osc) => osc.stop());
  alarm.oscillators = [];
  if (alarm.audioContext) {
    alarm.audioContext.close();
  }
  alarm.audioContext = null;
  alarm.gainNode = null;
}

function showAlert(eventId, alertVehicles) {
  alarm.activeEventId = eventId;
  updateAlertList(alertVehicles);
  elements.alertOverlay.classList.remove('hidden');
  startAlarm();
}

function hideAlert() {
  elements.alertOverlay.classList.add('hidden');
  stopAlarm();
}

async function acknowledgeAlert() {
  const eventId = alarm.activeEventId;
  if (!eventId) return;
  hideAlert();
  await fetch(API_ACK, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event_id: eventId }),
  });
  alarm.activeEventId = 0;
}

async function fetchStatus() {
  try {
    const response = await fetch(API_STATUS);
    const data = await response.json();
    elements.syncStatus.textContent = data.last_error ? 'Falha ao atualizar' : 'Sincronizado';
    elements.lastUpdate.textContent = `Última atualização: ${formatTimestamp(data.last_success)}`;
    elements.totalFila.textContent = data.total_fila;
    elements.seisCount.textContent = data.transportadora_seis.length;
    elements.sourceUrl.textContent = data.source;
    elements.vehicleCount.textContent = `${data.vehicles.length} veículos`;
    renderVehicles(data.vehicles);

    if (data.alert.active && data.alert.event_id !== alarm.activeEventId) {
      showAlert(data.alert.event_id, data.alert.vehicles);
    }
  } catch (error) {
    elements.syncStatus.textContent = 'Sem conexão com o backend';
  }
}

elements.confirmAlert.addEventListener('click', () => {
  acknowledgeAlert();
});

fetchStatus();
setInterval(fetchStatus, 10000);
