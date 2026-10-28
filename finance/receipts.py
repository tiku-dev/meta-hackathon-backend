"""Receipt PDF generation (ReportLab)."""
import io

from django.http import HttpResponse


def build_receipt_pdf(receipt) -> HttpResponse:
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A5)
    width, height = A5
    y = height - 20 * mm
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(15 * mm, y, "PAYMENT RECEIPT")
    y -= 8 * mm
    pdf.setFont("Helvetica", 9)
    student = receipt.invoice.student
    lines = [
        f"Receipt No:  {receipt.receipt_number}",
        f"Issued:      {receipt.issued_at:%Y-%m-%d %H:%M}",
        f"Student:     {student.full_name} ({student.matricule or '-'})",
        f"Invoice:     #{receipt.invoice_id}",
        f"Amount:      {receipt.amount} {receipt.currency}",
        f"Balance now: {receipt.invoice.balance} {receipt.invoice.currency}",
        "",
        "This receipt was generated from a provider-verified payment.",
    ]
    for line in lines:
        pdf.drawString(15 * mm, y, line)
        y -= 6 * mm
    pdf.showPage()
    pdf.save()
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{receipt.receipt_number}.pdf"'
    )
    return response
