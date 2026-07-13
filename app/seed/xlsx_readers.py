import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def _read_xlsx_first_sheet_rows(path: Path) -> list[list[str]]:
    namespace = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(path) as workbook_zip:
        shared_strings = _xlsx_shared_strings(workbook_zip, namespace)
        workbook = ET.fromstring(workbook_zip.read("xl/workbook.xml"))
        relationships = ET.fromstring(workbook_zip.read("xl/_rels/workbook.xml.rels"))
        relationship_targets = {
            relationship.attrib["Id"]: relationship.attrib["Target"]
            for relationship in relationships
        }
        first_sheet = workbook.find("a:sheets/a:sheet", namespace)
        if first_sheet is None:
            return []
        relationship_id = first_sheet.attrib[
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ]
        sheet_target = relationship_targets[relationship_id]
        sheet_path = (
            sheet_target.lstrip("/")
            if sheet_target.startswith("xl/")
            else f"xl/{sheet_target.lstrip('/')}"
        )
        sheet = ET.fromstring(workbook_zip.read(sheet_path))
        parsed_rows: list[list[str]] = []
        for row in sheet.findall(".//a:sheetData/a:row", namespace):
            values: list[str] = []
            for cell in row.findall("a:c", namespace):
                column_index = _xlsx_column_index(cell.attrib.get("r", ""))
                while len(values) <= column_index:
                    values.append("")
                values[column_index] = _xlsx_cell_value(cell, shared_strings, namespace)
            parsed_rows.append(values)
        return parsed_rows


def _xlsx_shared_strings(
    workbook_zip: zipfile.ZipFile, namespace: dict[str, str]
) -> list[str]:
    try:
        shared_root = ET.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(text.text or "" for text in item.findall(".//a:t", namespace))
        for item in shared_root.findall("a:si", namespace)
    ]


def _xlsx_cell_value(
    cell: ET.Element,
    shared_strings: list[str],
    namespace: dict[str, str],
) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//a:t", namespace)).strip()
    value_node = cell.find("a:v", namespace)
    value = "" if value_node is None else str(value_node.text or "")
    if cell_type == "s" and value:
        try:
            return shared_strings[int(value)].strip()
        except (IndexError, ValueError):
            return ""
    return value.strip()


def _xlsx_column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Z]+)", cell_reference.upper())
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _xlsx_cell(row: list[str], index: int) -> str:
    return str(row[index] if index < len(row) else "").strip()


