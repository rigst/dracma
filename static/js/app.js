// Centavo — JS clássico, sem módulo e sem build.

// Alternância de tema. O valor fica em localStorage e o script de pré-pintura
// no <head> o aplica antes do primeiro paint.
(function () {
  var botao = document.getElementById("alternar-tema");
  if (!botao) return;

  botao.addEventListener("click", function () {
    var raiz = document.documentElement;
    var escuro =
      raiz.dataset.theme === "dark" ||
      (!raiz.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var novo = escuro ? "light" : "dark";
    raiz.dataset.theme = novo;
    try {
      localStorage.setItem("tema", novo);
    } catch (e) {
      /* navegação privada: o tema vale só para esta página */
    }
  });
})();

// Conversa: rolar para a última fala quando o HTMX troca o histórico.
(function () {
  function aoFim() {
    var caixa = document.querySelector(".conversa");
    if (caixa) caixa.scrollTop = caixa.scrollHeight;
  }
  document.addEventListener("DOMContentLoaded", aoFim);
  document.body.addEventListener("htmx:afterSwap", aoFim);
})();
