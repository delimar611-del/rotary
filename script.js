const display = document.getElementById('display');
const expression = document.getElementById('expression');
const historyList = document.getElementById('history-list');
const clearHistoryBtn = document.getElementById('clear-history');

let currentValue = '0';
let previousValue = null;
let operator = null;
let shouldResetDisplay = false;
let history = JSON.parse(localStorage.getItem('calcHistory') || '[]');

function updateDisplay() {
  display.textContent = currentValue;
}

function formatResult(num) {
  if (num === Infinity || num === -Infinity || isNaN(num)) {
    return 'Error';
  }
  const str = String(num);
  if (str.length > 12) {
    return parseFloat(num.toPrecision(10)).toString();
  }
  return str;
}

function getOperatorSymbol(op) {
  const symbols = { '/': '\u00F7', '*': '\u00D7', '-': '\u2212', '+': '+' };
  return symbols[op] || op;
}

function calculate(a, op, b) {
  const numA = parseFloat(a);
  const numB = parseFloat(b);
  switch (op) {
    case '+': return numA + numB;
    case '-': return numA - numB;
    case '*': return numA * numB;
    case '/': return numB === 0 ? NaN : numA / numB;
    default: return numB;
  }
}

function renderHistory() {
  if (history.length === 0) {
    historyList.innerHTML = '<div class="history-empty">No calculations yet</div>';
    return;
  }
  historyList.innerHTML = history.map((item, index) =>
    `<div class="history-item" data-index="${index}">
      <div class="history-expression">${item.expr}</div>
      <div class="history-result">${item.result}</div>
    </div>`
  ).join('');
}

function addToHistory(expr, result) {
  history.unshift({ expr, result });
  if (history.length > 50) history.pop();
  localStorage.setItem('calcHistory', JSON.stringify(history));
  renderHistory();
}

function handleNumber(value) {
  if (shouldResetDisplay) {
    currentValue = value;
    shouldResetDisplay = false;
  } else {
    currentValue = currentValue === '0' ? value : currentValue + value;
  }
  updateDisplay();
}

function handleOperator(op) {
  if (previousValue !== null && operator && !shouldResetDisplay) {
    const result = calculate(previousValue, operator, currentValue);
    currentValue = formatResult(result);
    updateDisplay();
  }
  previousValue = currentValue;
  operator = op;
  shouldResetDisplay = true;
  expression.textContent = `${previousValue} ${getOperatorSymbol(op)}`;
}

function handleEquals() {
  if (previousValue === null || operator === null) return;
  const expr = `${previousValue} ${getOperatorSymbol(operator)} ${currentValue}`;
  const result = calculate(previousValue, operator, currentValue);
  const formattedResult = formatResult(result);
  expression.textContent = `${expr} =`;
  currentValue = formattedResult;
  addToHistory(expr, formattedResult);
  previousValue = null;
  operator = null;
  shouldResetDisplay = true;
  updateDisplay();
}

function handleClear() {
  currentValue = '0';
  previousValue = null;
  operator = null;
  shouldResetDisplay = false;
  expression.textContent = '';
  updateDisplay();
}

function handleDecimal() {
  if (shouldResetDisplay) {
    currentValue = '0.';
    shouldResetDisplay = false;
  } else if (!currentValue.includes('.')) {
    currentValue += '.';
  }
  updateDisplay();
}

function handleToggleSign() {
  if (currentValue !== '0') {
    currentValue = currentValue.startsWith('-')
      ? currentValue.slice(1)
      : '-' + currentValue;
    updateDisplay();
  }
}

function handlePercent() {
  currentValue = formatResult(parseFloat(currentValue) / 100);
  updateDisplay();
}

document.querySelector('.buttons').addEventListener('click', (e) => {
  const btn = e.target.closest('.btn');
  if (!btn) return;

  const action = btn.dataset.action;
  const value = btn.dataset.value;

  switch (action) {
    case 'number': handleNumber(value); break;
    case 'operator': handleOperator(value); break;
    case 'equals': handleEquals(); break;
    case 'clear': handleClear(); break;
    case 'decimal': handleDecimal(); break;
    case 'toggle-sign': handleToggleSign(); break;
    case 'percent': handlePercent(); break;
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key >= '0' && e.key <= '9') handleNumber(e.key);
  else if (e.key === '.') handleDecimal();
  else if (['+', '-', '*', '/'].includes(e.key)) handleOperator(e.key);
  else if (e.key === 'Enter' || e.key === '=') handleEquals();
  else if (e.key === 'Escape') handleClear();
  else if (e.key === '%') handlePercent();
});

// Click a history item to load its result
historyList.addEventListener('click', (e) => {
  const item = e.target.closest('.history-item');
  if (!item) return;
  const index = parseInt(item.dataset.index);
  currentValue = history[index].result;
  shouldResetDisplay = true;
  updateDisplay();
});

// Clear history
clearHistoryBtn.addEventListener('click', () => {
  history = [];
  localStorage.setItem('calcHistory', JSON.stringify(history));
  renderHistory();
});

// Load history on start
renderHistory();
