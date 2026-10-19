import pytest
from fastapi import HTTPException
from backend.main import filename_title, folder_id_from_input, is_video, RANGE_RE

@pytest.mark.parametrize('filename,title,year', [
    ('Perfect.Days.2023.1080p.mkv','Perfect Days','2023'),
    ('La.Vida.Es.Bella.1997.bluray.mp4','La Vida Es Bella','1997'),
    ('Stalker.webm','Stalker',''),
])
def test_title(filename,title,year):
    assert filename_title(filename)==(title,year)

def test_folder_id():
    assert folder_id_from_input('https://drive.google.com/drive/folders/1abCDEfghIJklMNop-2xyz?usp=sharing')=='1abCDEfghIJklMNop-2xyz'
    with pytest.raises(HTTPException):
        folder_id_from_input('https://evil.example/folders/1abCDEfghIJklMNop-2xyz')

def test_range_header():
    assert RANGE_RE.fullmatch('bytes=100-')
    assert RANGE_RE.fullmatch('bytes=-500')
    assert not RANGE_RE.fullmatch('bytes=0-10,20-30')

def test_video_extension():
    assert is_video({'name':'film.mkv','mimeType':'application/octet-stream'})
    assert not is_video({'name':'notes.pdf','mimeType':'application/pdf'})
