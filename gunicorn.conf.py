# Configuração do gunicorn, lida por descoberta: a unidade não passa
# `--config`, e sem ela o gunicorn procura `./gunicorn.conf.py` a partir do
# WorkingDirectory.
#
# Existe por um motivo só. O gunicorn 26 abre um socket de controle, para o
# `gunicornc`, cujo caminho padrão é `$XDG_RUNTIME_DIR/gunicorn.ctl` e, sem
# essa variável (o caso sob systemd), cai em `~/.gunicorn/gunicorn.ctl`.
#
# Os serviços deste servidor rodam como `rod` e resolviam todos para o MESMO
# arquivo. Socket unix tem um dono só: quem sobe por último fica com ele, e a
# partir daí o `gunicornc` fala com o app errado sem avisar nada.
#
# Exige RESTART, não reload: o SIGHUP relê este arquivo, mas o arbiter não
# reinicia o servidor de controle junto.
control_socket = "/home/rod/.gunicorn/dracma.ctl"
