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
    } catch {
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

// Divisão do gasto: mostra o bloco quando o lançamento é da casa, e dentro
// dele só as colunas do modo escolhido. Sem isto a caixa abre com dois campos
// por pessoa, e só um deles importa.
(function () {
  function colunas(bloco, modo) {
    var tabela = bloco.querySelector("[data-partes]");
    if (!tabela) return;
    var porPessoa = modo === "percentual" || modo === "valor";
    tabela.hidden = !porPessoa;
    tabela.querySelectorAll("[data-col]").forEach(function (celula) {
      celula.hidden = celula.dataset.col !== modo;
    });
  }

  function sincronizar(raiz) {
    var bloco = raiz.querySelector("[data-divisao]");
    if (!bloco) return;

    var daCasa = raiz.querySelector('input[name="compartilhada"][value="1"]');
    var modo = bloco.querySelector('[name="modo_rateio"]');

    function aplicar() {
      // Sem radio na tela (espaço de uma pessoa), não há o que dividir.
      bloco.hidden = !(daCasa && daCasa.checked);
      colunas(bloco, modo ? modo.value : "padrao");
    }

    raiz.querySelectorAll('input[name="compartilhada"]').forEach(function (opcao) {
      opcao.addEventListener("change", aplicar);
    });
    if (modo) modo.addEventListener("change", aplicar);
    aplicar();
  }

  document.body.addEventListener("htmx:afterSwap", function (evento) {
    if (evento.detail.target.id === "dialogo-corpo") sincronizar(evento.detail.target);
  });
})();
