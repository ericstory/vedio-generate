const error = document.querySelector('#login-error');
if (new URLSearchParams(location.search).get('error') === 'invalid') {
  error.textContent = '账号或密码不正确';
}
