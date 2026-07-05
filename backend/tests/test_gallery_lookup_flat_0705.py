"""图库主图查找: 相机直导的扁平文件夹(无「主图」子目录)兜底取根目录第一张 (用户 2026-07-05)。

现象: 4个新拍产品文件夹里是 DSCF*.JPG 平铺, 没有「主图」子目录 → main_image_rel 返 None
→ 产品图库主图关联不上(退回淘宝图)。修: 无「主图」时取文件夹根第一张。
"""
from app.services import gallery_lookup as gl


def _img(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8\xff\xe0fake")   # 只测路径解析, 不需真图


def test_flat_folder_falls_back_to_root_image(tmp_path, monkeypatch):
    monkeypatch.setenv("GALLERY_ROOT", str(tmp_path))
    folder = tmp_path / "PFG26210060102 孚格中古岩板餐桌"
    _img(folder / "DSCF6493.JPG")
    _img(folder / "DSCF6491.JPG")
    rel = gl.main_image_rel("PFG26210060102")
    assert rel is not None
    assert rel.replace("\\", "/").endswith("DSCF6491.JPG")   # 排序第一张


def test_zhu_subfolder_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("GALLERY_ROOT", str(tmp_path))
    folder = tmp_path / "PPS11111111111 测试"
    _img(folder / "DSCF9999.JPG")                # 根目录图(应被忽略)
    _img(folder / "主图" / "1-1" / "main.jpg")    # 主图/1-1 应优先
    rel = gl.main_image_rel("PPS11111111111")
    assert rel is not None
    assert "主图" in rel and rel.replace("\\", "/").endswith("main.jpg")


def test_empty_zhu_falls_back_to_root(tmp_path, monkeypatch):
    """有「主图」子目录但为空 → 仍兜底到根图, 不返 None。"""
    monkeypatch.setenv("GALLERY_ROOT", str(tmp_path))
    folder = tmp_path / "PPS22222222222 测试"
    (folder / "主图").mkdir(parents=True)
    _img(folder / "DSCF0001.JPG")
    rel = gl.main_image_rel("PPS22222222222")
    assert rel is not None and rel.replace("\\", "/").endswith("DSCF0001.JPG")


def test_no_images_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("GALLERY_ROOT", str(tmp_path))
    (tmp_path / "PFG00000000000 空文件夹").mkdir(parents=True)
    assert gl.main_image_rel("PFG00000000000") is None
