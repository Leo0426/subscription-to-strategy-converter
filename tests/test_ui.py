from fastapi.testclient import TestClient

from app.main import app


def test_index_page_is_served() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Subflow" in response.text
    assert "/static/app.js" in response.text
    assert "策略指挥室" in response.text
    assert "生成配置" in response.text
    assert "高级选项" in response.text
    assert "Mihomo 订阅链接" in response.text
    assert "yaml-view-tabs" in response.text
    assert "新增分组" in response.text
    assert "模板文件库" in response.text
    assert "规则分析" in response.text
    assert "流量模拟" in response.text
    assert "规则编排" in response.text
    assert "自定义规则" in response.text
    assert "规则集目录" in response.text
    assert "策略组调试" in response.text
    assert "配置预览" in response.text
    assert "节点列表" in response.text
    assert "浏览社区配置" in response.text
    assert "community-browser" in response.text
    assert "subconverter-config" not in response.text
