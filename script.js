const display = document.getElementById('display');
const expression = document.getElementById('expression');

let currentValue = '0';
let previousValue = null;
let operator = null;
let shouldResetDisplay = false;

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
  const result = calculate(previousValue, operator, currentValue);
  expression.textContent = `${previousValue} ${getOperatorSymbol(operator)} ${currentValue} =`;
  currentValue = formatResult(result);
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
