// Adds a show/hide eye button to every password field.
// Pure convenience: without JavaScript the fields work exactly as before, just without the button.
document.querySelectorAll('input[type="password"]').forEach(function (input) {
  var group = document.createElement('div');
  group.className = 'input-group';
  input.parentNode.insertBefore(group, input);
  group.appendChild(input);

  var button = document.createElement('button');
  button.type = 'button';  // never submits the form
  button.className = 'btn btn-glass password-toggle';
  button.setAttribute('aria-label', 'Show password');
  button.innerHTML = '<i class="bi bi-eye"></i>';
  group.appendChild(button);

  button.addEventListener('click', function () {
    var hidden = input.type === 'password';
    input.type = hidden ? 'text' : 'password';
    button.setAttribute('aria-label', hidden ? 'Hide password' : 'Show password');
    button.innerHTML = hidden ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
    input.focus();
  });
});
