## Internacionalização

O projecto passa a usar o mecanismo nativo de i18n do Django com catálogos em `locale/`.

### Estrutura actual

- `locale/en/LC_MESSAGES/django.po`: catálogo fonte para inglês.
- `locale/en/LC_MESSAGES/django.mo`: catálogo compilado carregado pelo Django.
- `scripts/sync_locale_catalog.py`: sincroniza o catálogo inglês a partir das traduções já usadas no código (`translate_pair`, `ui_text` e `{% ui %}`).

### Actualizar o catálogo inglês

```powershell
venv\Scripts\python.exe scripts\sync_locale_catalog.py
```

### Adicionar uma nova língua

1. Criar a pasta `locale/<codigo>/LC_MESSAGES/`.
2. Copiar `locale/en/LC_MESSAGES/django.po` como base.
3. Traduzir apenas os `msgstr`.
4. Compilar o ficheiro `.po` para `.mo`.

```powershell
venv\Scripts\python.exe scripts\compile_locale.py
```

Nota: a tag `{% ui %}` continua disponível apenas como camada de compatibilidade. O ponto de verdade para novas línguas agora é o catálogo em `locale/`.
