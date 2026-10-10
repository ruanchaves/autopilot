# autopilot

Skills do Claude Code para a busca de emprego. Cada pasta é uma skill; `~/.claude/skills/<nome>` é uma
junção de diretório que aponta para cá, então `/job-source-sweep`, `/job-autopilot` e
`/recruiter-outreach` carregam a partir deste repositório.

- `job-source-sweep/` varre ~80 fontes de vagas, enriquece cada vaga (descrição, elegibilidade por
  país, aderência ao currículo) e publica o explorador de vagas. As outras duas skills importam os
  scripts dela como pasta irmã.
- `job-autopilot/` pontua as vagas, adapta o currículo, preenche os formulários de candidatura até o
  botão de envio (nunca envia) e registra no applika as candidaturas que você confirmar.
- `recruiter-outreach/` monta um banco de recrutadores do LinkedIn com pontuação e contata os melhores
  dentro dos limites diários.

## Deixe do seu jeito: `config.yaml`

Tudo o que é pessoal está em **`config.yaml`**, na raiz do repositório, e nada pessoal fica fixo no
código das skills: quem é o candidato (contatos, país e região, fuso horário, anos de experiência,
pitch, histórias, tipos de contratação, tipos de contrato a evitar), os caminhos do currículo, o cargo
e a remuneração alvo, os empregadores atuais para os quais nunca se candidatar, a pasta de dados
(`home`), o runner do Python e os ajustes de cada skill (`sweep`, `autopilot`, `recruiter_outreach`,
`tracker`). Faça um fork do repositório, edite o arquivo (todas as chaves estão comentadas) e rode
`python3 job-source-sweep/scripts/jobsweep.py config` para ver o resultado consolidado.
`job-source-sweep/scripts/userconfig.py` é o carregador compartilhado pelas três skills; o PyYAML é
obrigatório.

Os dados por usuário (varreduras, caches, livro-razão de candidaturas, banco de respostas, leads de
recrutadores) ficam fora do repositório, na pasta `home` definida em `config.yaml`. O único arquivo
gravado lá como estado é `config.json`, com a URL do artefato do explorador. A skill `tailor-resume`,
chamada pelo autopilot, continua em `~/.claude/skills`.

Execute os scripts com o runner definido em `config.yaml`; por exemplo, com `python: wsl`:
`wsl.exe -e python3 /mnt/d/dev/autopilot/job-autopilot/scripts/autopilot.py status` (`--home` assume a
pasta configurada).
