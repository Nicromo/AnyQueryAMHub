"""
Обработка файлов - загрузка и экспорт данных
Поддержка: CSV, Excel (XLSX), PDF
"""

import os
import io
import logging
from datetime import datetime
from typing import List, Optional, BinaryIO
from enum import Enum

import pandas as pd

logger = logging.getLogger(__name__)


class FileFormat(str, Enum):
    CSV = "csv"
    EXCEL = "xlsx"
    PDF = "pdf"


class FileProcessor:
    """Обработка файлов"""

    @staticmethod
    def parse_csv(file_content: bytes) -> List[dict]:
        """Парсить CSV файл в список словарей"""
        try:
            df = pd.read_csv(io.BytesIO(file_content))
            return df.to_dict("records")
        except Exception as e:
            logger.error(f"CSV parse error: {e}")
            return []

    @staticmethod
    def parse_excel(file_content: bytes) -> List[dict]:
        """Парсить Excel файл в список словарей"""
        try:
            df = pd.read_excel(io.BytesIO(file_content))
            return df.to_dict("records")
        except Exception as e:
            logger.error(f"Excel parse error: {e}")
            return []

    @staticmethod
    def validate_client_data(data: List[dict]) -> tuple[bool, str]:
        """Валидировать данные клиентов"""
        required_fields = ["name", "email"]
        
        for i, row in enumerate(data):
            for field in required_fields:
                if field not in row or not row[field]:
                    return False, f"Row {i+1}: Missing required field '{field}'"
            
            # Валидация email
            if "@" not in str(row.get("email", "")):
                return False, f"Row {i+1}: Invalid email format"
        
        return True, "OK"

    @staticmethod
    def validate_task_data(data: List[dict]) -> tuple[bool, str]:
        """Валидировать данные задач"""
        required_fields = ["title", "client_name"]
        
        for i, row in enumerate(data):
            for field in required_fields:
                if field not in row or not row[field]:
                    return False, f"Row {i+1}: Missing required field '{field}'"
        
        return True, "OK"

    @staticmethod
    def to_csv(data: List[dict]) -> bytes:
        """Экспортировать данные в CSV"""
        try:
            df = pd.DataFrame(data)
            return df.to_csv(index=False).encode("utf-8")
        except Exception as e:
            logger.error(f"CSV export error: {e}")
            return b""

    @staticmethod
    def to_excel(data: List[dict], sheet_name: str = "Data") -> bytes:
        """Экспортировать данные в Excel"""
        try:
            df = pd.DataFrame(data)
            
            # Create Excel file in memory
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                
                # Format
                worksheet = writer.sheets[sheet_name]
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width
            
            output.seek(0)
            return output.getvalue()
        except Exception as e:
            logger.error(f"Excel export error: {e}")
            return b""

    @staticmethod
    def to_pdf(data: List[dict], title: str = "Report") -> bytes:
        """Экспортировать данные в PDF"""
        try:
            from reportlab.lib.pagesizes import letter, A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib import colors
            from datetime import datetime

            # Create PDF in memory
            pdf_buffer = io.BytesIO()
            doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, topMargin=0.5 * inch)

            # Title
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                "CustomTitle",
                parent=styles["Heading1"],
                fontSize=16,
                textColor=colors.HexColor("#1a5490"),
                spaceAfter=12,
            )

            # Data to table
            if not data:
                return b""

            # Header
            headers = list(data[0].keys())
            table_data = [headers]

            # Rows
            for row in data[:100]:  # Limit to 100 rows for PDF
                table_data.append([str(row.get(h, "")) for h in headers])

            # Create table
            table = Table(table_data, colWidths=[doc.width / len(headers)] * len(headers))

            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a5490")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 12),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                        ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                        ("GRID", (0, 0), (-1, -1), 1, colors.black),
                        ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ]
                )
            )

            # Build PDF
            elements = [
                Paragraph(f"<b>{title}</b>", title_style),
                Spacer(1, 0.2 * inch),
                Paragraph(f"<i>Сгенерировано: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>", styles["Normal"]),
                Spacer(1, 0.2 * inch),
                table,
            ]

            doc.build(elements)
            pdf_buffer.seek(0)
            return pdf_buffer.getvalue()

        except Exception as e:
            logger.error(f"PDF export error: {e}")
            return b""


class BulkImporter:
    """Импорт данных в bulk"""

    @staticmethod
    def detect_format(filename: str) -> Optional[FileFormat]:
        """Определить формат файла"""
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".csv":
            return FileFormat.CSV
        elif ext in [".xlsx", ".xls"]:
            return FileFormat.EXCEL
        return None

    @staticmethod
    def import_clients(file_content: bytes, filename: str) -> tuple[bool, str, List[dict]]:
        """Импортировать клиентов из файла"""
        fmt = BulkImporter.detect_format(filename)
        
        if fmt == FileFormat.CSV:
            data = FileProcessor.parse_csv(file_content)
        elif fmt == FileFormat.EXCEL:
            data = FileProcessor.parse_excel(file_content)
        else:
            return False, "Unsupported file format", []
        
        # Валидировать
        valid, msg = FileProcessor.validate_client_data(data)
        if not valid:
            return False, msg, []
        
        logger.info(f"✅ Imported {len(data)} clients from {filename}")
        return True, "OK", data

    @staticmethod
    def import_tasks(file_content: bytes, filename: str) -> tuple[bool, str, List[dict]]:
        """Импортировать задачи из файла"""
        fmt = BulkImporter.detect_format(filename)
        
        if fmt == FileFormat.CSV:
            data = FileProcessor.parse_csv(file_content)
        elif fmt == FileFormat.EXCEL:
            data = FileProcessor.parse_excel(file_content)
        else:
            return False, "Unsupported file format", []
        
        # Валидировать
        valid, msg = FileProcessor.validate_task_data(data)
        if not valid:
            return False, msg, []
        
        logger.info(f"✅ Imported {len(data)} tasks from {filename}")
        return True, "OK", data


class BulkExporter:
    """Экспорт данных в bulk"""

    @staticmethod
    def export_clients(clients: List[dict], format: FileFormat = FileFormat.EXCEL) -> bytes:
        """Экспортировать клиентов"""
        # Prepare data
        export_data = []
        for client in clients:
            export_data.append(
                {
                    "Имя": client.get("name", ""),
                    "Email": client.get("email", ""),
                    "Телефон": client.get("phone", ""),
                    "Сегмент": client.get("segment", ""),
                    "Статус": client.get("status", ""),
                    "Health Score": client.get("health_score", 0),
                    "Менеджер": client.get("manager_name", ""),
                }
            )

        if format == FileFormat.CSV:
            return FileProcessor.to_csv(export_data)
        elif format == FileFormat.PDF:
            return FileProcessor.to_pdf(export_data, title="Клиенты")
        else:  # EXCEL
            return FileProcessor.to_excel(export_data, sheet_name="Клиенты")

    @staticmethod
    def export_tasks(tasks: List[dict], format: FileFormat = FileFormat.EXCEL) -> bytes:
        """Экспортировать задачи"""
        # Prepare data
        export_data = []
        for task in tasks:
            export_data.append(
                {
                    "Задача": task.get("title", ""),
                    "Клиент": task.get("client_name", ""),
                    "Приоритет": task.get("priority", ""),
                    "Статус": task.get("status", ""),
                    "Дата создания": task.get("created_at", ""),
                    "Дедлайн": task.get("due_date", ""),
                    "Назначено": task.get("assigned_to", ""),
                }
            )

        if format == FileFormat.CSV:
            return FileProcessor.to_csv(export_data)
        elif format == FileFormat.PDF:
            return FileProcessor.to_pdf(export_data, title="Задачи")
        else:  # EXCEL
            return FileProcessor.to_excel(export_data, sheet_name="Задачи")

    @staticmethod
    def export_meetings(meetings: List[dict], format: FileFormat = FileFormat.EXCEL) -> bytes:
        """Экспортировать встречи"""
        # Prepare data
        export_data = []
        for meeting in meetings:
            export_data.append(
                {
                    "Дата": meeting.get("meeting_date", ""),
                    "Клиент": meeting.get("client_name", ""),
                    "Тип": meeting.get("meeting_type", ""),
                    "Результат": meeting.get("summary", "")[:50],
                    "Длительность": meeting.get("duration_minutes", ""),
                }
            )

        if format == FileFormat.CSV:
            return FileProcessor.to_csv(export_data)
        elif format == FileFormat.PDF:
            return FileProcessor.to_pdf(export_data, title="Встречи")
        else:  # EXCEL
            return FileProcessor.to_excel(export_data, sheet_name="Встречи")
