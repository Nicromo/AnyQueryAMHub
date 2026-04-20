# Наработки коллег

Сюда кладём чужой код/наброски/эксперименты, которые могут быть полезны для AM Hub, но пока не интегрированы в основной код.

Каждый подпроект — в отдельной подпапке:
- `time_case_board/` — доска кейсов Time от коллеги (залить из `C:\Users\dimaa\Downloads\time_case_board`).

## Как залить папку с Windows

Открой PowerShell в `C:\Users\dimaa\Downloads\time_case_board` и выполни:

```powershell
# 1) Клонируешь текущую ветку AM Hub рядом
cd C:\Users\dimaa\Downloads
git clone -b claude/colleague-drafts-folder https://github.com/Nicromo/AnyQueryAMHub.git amhub-drafts
cd amhub-drafts

# 2) Копируешь содержимое папки коллеги в "наработки/time_case_board"
xcopy /E /I /Y "C:\Users\dimaa\Downloads\time_case_board" "наработки\time_case_board"

# 3) Коммитишь + пушишь
git add наработки
git commit -m "add colleague time_case_board drafts"
git push origin claude/colleague-drafts-folder
```

После этого скажи мне — я прочитаю и разберу, что оттуда вытащить в основной код.
