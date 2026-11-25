"""Exercise the Windows analysis runtime without a model, database, or employee data."""
import asyncio
from io import BytesIO
import sys
from types import SimpleNamespace
from uuid import uuid4

from openpyxl import load_workbook

from harness.config import Settings, WindowsIsolationSettings
from harness.isolation.backend import isolation_backend, prepare_isolated_run
from harness.runtime.session_kernel import CellInput, SessionKernels


async def check(settings):
    principal = 'acceptance-' + uuid4().hex
    owner = (principal, 'chat-a')
    other = (principal, 'chat-b')
    kernels = SessionKernels()

    def context(identity):
        return SimpleNamespace(run={'principal_id': identity[0], 'session_id': identity[1]},
                               settings=settings)

    async def cell(identity, code):
        result = await asyncio.wait_for(kernels.execute(CellInput(code=code), context(identity)), 100)
        if result.status != 'success':
            raise RuntimeError(result.scope['execute_code']['output'])
        return result

    await asyncio.to_thread(prepare_isolated_run, settings.isolation, 'acceptance', principal,
                            session_id=owner[1])
    writer = isolation_backend(settings.isolation, 'acceptance', principal, session_id=owner[1])
    path = await writer.upload_bytes(b'x,y,bin\n1,1,0\n2,1,1\n')
    try:
        await cell(owner, f"points = pd.read_csv({str(path)!r}); assert points['bin'].tolist() == [0, 1]")
        result = await cell(owner, """
from io import BytesIO
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SheetImage
from PIL import Image
picture = Image.new('RGB', (2, 1))
picture.putdata([(0, 128, 0) if b == 0 else (255, 0, 0) for b in points['bin']])
png = BytesIO()
picture.save(png, format='PNG')
png.seek(0)
book = Workbook()
book.active.append(list(points.columns))
for row in points.itertuples(index=False, name=None):
    book.active.append(row)
book.active.add_image(SheetImage(png), 'E1')
book.save(publish_directory / '웨이퍼맵.xlsx')
""")
        file, = [a for a in result.artifacts if a.get('generated_file')]
        with file['data'] as data:
            book = load_workbook(BytesIO(data.read()))
        try:
            assert file['title'] == '웨이퍼맵.xlsx'
            assert list(book.active.values) == [('x','y','bin'), (1,1,0), (2,1,1)]
            image, = book.active._images
            from PIL import Image
            with Image.open(BytesIO(image._data())) as pixels:
                assert list(pixels.convert('RGB').getdata()) == [(0,128,0), (255,0,0)]
        finally:
            book.close()
        await cell(other, f"""
assert 'points' not in globals()
try:
    open({str(path)!r}, 'rb').close()
except (PermissionError, FileNotFoundError):
    pass
else:
    raise AssertionError('another chat could read the input')
""")
    finally:
        await kernels.close()
    print("PASS: Windows AppContainer, scoped CSV, persistent Python, XLSX image and chat separation")


def main():
    if sys.platform != 'win32':
        raise SystemExit('Run this acceptance check on Windows.')
    from dotenv import load_dotenv
    load_dotenv()
    settings = Settings.from_env()
    if not isinstance(settings.isolation, WindowsIsolationSettings):
        raise SystemExit('Select the Windows AppContainer backend for this check.')
    asyncio.run(check(settings))


if __name__ == '__main__':
    main()
