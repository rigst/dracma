// Dracma — JS clássico, sem módulo e sem build.

// Tema. O valor fica em localStorage e o script de pré-pintura no <head> o
// aplica antes do primeiro paint.
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

// Diálogos. O conteúdo vem do servidor por HTMX e é injetado em #dialogo-corpo;
// o <dialog> só abre depois que o HTML chegou, senão a caixa pisca vazia.
(function () {
  var dialogo = document.getElementById("dialogo");
  if (!dialogo) return;

  document.body.addEventListener("htmx:afterSwap", function (evento) {
    if (evento.detail.target.id === "dialogo-corpo" && !dialogo.open) {
      dialogo.showModal();
      var primeiro = dialogo.querySelector("input, select, textarea");
      if (primeiro) primeiro.focus();
    }
  });

  // O servidor manda HX-Trigger: gravado quando a escrita deu certo.
  document.body.addEventListener("gravado", function () {
    if (dialogo.open) dialogo.close();
  });

  dialogo.addEventListener("click", function (evento) {
    // Clique no backdrop: o alvo é o próprio <dialog>, porque o conteúdo está
    // num filho. Fechar aqui evita prender quem não achou o botão.
    if (evento.target === dialogo) dialogo.close();
  });

  dialogo.addEventListener("close", function () {
    document.getElementById("dialogo-corpo").innerHTML = "";
  });

  document.body.addEventListener("click", function (evento) {
    if (evento.target.closest("[data-fechar]")) dialogo.close();
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
