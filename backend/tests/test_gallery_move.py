from fastapi import HTTPException
import pytest

from app.api import gallery


PRODUCT_FOLDER = "PPS24210070901 测试产品"


def _prepare(tmp_path, monkeypatch):
    monkeypatch.setenv("GALLERY_ROOT", str(tmp_path))
    product = tmp_path / PRODUCT_FOLDER
    product.mkdir()
    return product


def test_move_images_from_root_to_new_group(tmp_path, monkeypatch):
    product = _prepare(tmp_path, monkeypatch)
    (product / "正面.jpg").write_bytes(b"front")
    (product / "侧面.png").write_bytes(b"side")

    result = gallery.move_images(gallery.MoveImagesRequest(
        folder=PRODUCT_FOLDER,
        paths=[f"{PRODUCT_FOLDER}/正面.jpg", f"{PRODUCT_FOLDER}/侧面.png"],
        target_group="安装细节图",
    ))

    assert result["moved"] == 2
    assert result["conflicts"] == 0
    assert result["failed"] == 0
    assert not (product / "正面.jpg").exists()
    assert (product / "安装细节图" / "正面.jpg").read_bytes() == b"front"
    assert (product / "安装细节图" / "侧面.png").read_bytes() == b"side"


def test_move_images_never_overwrites_same_name(tmp_path, monkeypatch):
    product = _prepare(tmp_path, monkeypatch)
    target = product / "场景图"
    target.mkdir()
    (product / "同名.jpg").write_bytes(b"new")
    (target / "同名.jpg").write_bytes(b"existing")

    result = gallery.move_images(gallery.MoveImagesRequest(
        folder=PRODUCT_FOLDER,
        paths=[f"{PRODUCT_FOLDER}/同名.jpg"],
        target_group="场景图",
    ))

    assert result["moved"] == 0
    assert result["conflicts"] == 1
    assert (product / "同名.jpg").read_bytes() == b"new"
    assert (target / "同名.jpg").read_bytes() == b"existing"


def test_move_images_rejects_path_from_another_product(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    other = tmp_path / "PPS99999999999 其他产品"
    other.mkdir()
    (other / "图片.jpg").write_bytes(b"other")

    with pytest.raises(HTTPException) as exc_info:
        gallery.move_images(gallery.MoveImagesRequest(
            folder=PRODUCT_FOLDER,
            paths=["PPS99999999999 其他产品/图片.jpg"],
            target_group="场景图",
        ))

    assert exc_info.value.status_code == 403
    assert (other / "图片.jpg").exists()


@pytest.mark.parametrize("group", ["../越界", ".隐藏", "a/b", ""])
def test_move_images_rejects_unsafe_target_group(tmp_path, monkeypatch, group):
    product = _prepare(tmp_path, monkeypatch)
    (product / "图片.jpg").write_bytes(b"image")

    with pytest.raises(HTTPException) as exc_info:
        gallery.move_images(gallery.MoveImagesRequest(
            folder=PRODUCT_FOLDER,
            paths=[f"{PRODUCT_FOLDER}/图片.jpg"],
            target_group=group,
        ))

    assert exc_info.value.status_code == 400
    assert (product / "图片.jpg").exists()
