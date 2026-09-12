"""Fill only explicitly approved employer answers; unknown questions need review."""
import hashlib
import re
from pathlib import Path


def question_key(question):
    return 'form.'+hashlib.sha256(' '.join(question.casefold().split()).strip(' *:?').encode()).hexdigest()


def answer_for_question(question,repository,*,portal):
    return repository.lookup(question_key(question),{'portal':portal}) if repository is not None else None


def fill_approved_questions(scope,repository,*,portal,cover_note=''):
    for field in scope.locator("textarea:visible,input[name*='customQuestion']:visible").all():
        if field.input_value().strip():
            continue
        label=field.evaluate("el => el.getAttribute('aria-label') || [...(el.labels || [])].map(x=>x.innerText).join(' ') || el.getAttribute('placeholder') || ''")
        answer=answer_for_question(label,repository,portal=portal) if label else None
        if answer is None and re.fullmatch(r'\s*(cover (?:letter|note)|message to (?:the )?hiring manager)\s*[*:]?\s*',label,re.I):
            answer=cover_note or None
        if answer is None:
            return False
        field.fill(str(answer))
    return True


def upload_approved_resume(scope,resume_path):
    if not Path(resume_path).is_file():
        return False
    try:
        inputs=scope.locator('input[type=file]')
        if inputs.count()!=1:
            return False
        inputs.set_input_files(str(resume_path),timeout=8000)
        return inputs.evaluate('el => el.files.length === 1 && el.files[0].name')==Path(resume_path).name
    except Exception:
        return False
