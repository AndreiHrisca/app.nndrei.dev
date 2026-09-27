import csv
import io
import uuid
from datetime import date,timedelta
from decimal import ROUND_CEILING,Decimal
from django.db.models import Sum
from django.http import Http404,HttpResponse,FileResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from .access import scoped
from .auth_views import roles
from .models import Transaction,ClientCharge,RecurringExpense,Commission
from .forms import TransactionForm,ChargeForm,ExpenseForm,CommissionForm
from .finance import advance_date

def period(request):
    try: start=date.fromisoformat(request.GET.get('month','')+'-01')
    except ValueError: start=timezone.localdate().replace(day=1)
    return start,advance_date(start,'monthly')

def selected_transactions(request):
    start,end=period(request)
    qs=Transaction.objects.visible_to(request.user).filter(date__gte=start,date__lt=end)
    business=request.GET.get('business','')
    if business:
        try:
            qs=qs.filter(business__public_id=uuid.UUID(business))
        except ValueError:
            qs=qs.none()
    return qs

def chart_data(user):
    current=timezone.localdate().replace(day=1)
    months=[]
    for offset in range(5,-1,-1):
        index=current.year*12+current.month-1-offset
        start=date(index//12,index%12+1,1); end=advance_date(start,'monthly')
        qs=Transaction.objects.visible_to(user).filter(date__gte=start,date__lt=end)
        income=qs.filter(amount__gt=0).aggregate(v=Sum('amount'))['v'] or Decimal(0)
        expense=-(qs.filter(amount__lt=0).aggregate(v=Sum('amount'))['v'] or Decimal(0))
        months.append({'label':start.strftime('%m/%Y'),'start':start,'income':income,'expense':expense})
    # The bars share a rounded scale so the axis reads 0 · 100 · 200 €, not the raw maximum.
    top,step=axis_scale(max([x[k] for x in months for k in ['income','expense']]))
    for i,row in enumerate(months):
        x=CHART_LEFT+i*CHART_GROUP+(CHART_GROUP-54)//2  # integers: a localized float would print "114,0"
        row.update(x=x,expense_x=x+30,short_x=x+27,ih=float(row['income']/top*CHART_HEIGHT),eh=float(row['expense']/top*CHART_HEIGHT))
        row['iy']=CHART_BASE-row['ih']; row['ey']=CHART_BASE-row['eh']
    ticks=[{'y':CHART_BASE-float(value/top*CHART_HEIGHT),'label':f'{value:,.0f}'.replace(',','.')+' €'} for value in (step*n for n in range(int(top/step)+1))]
    return {'months':months,'ticks':ticks,'left':CHART_LEFT}

# Plot area inside the 600×210 viewBox: amounts on the left, months below.
CHART_LEFT,CHART_GROUP,CHART_BASE,CHART_HEIGHT=100,82,160,140

def axis_scale(maximum):
    """Top of the axis and the step between guide lines: 1, 2, 2.5 or 5 × 10ⁿ, about five steps
    (100 € steps up to 500 €; wider only when there are more lines than fit on a phone)."""
    if maximum<=0: return Decimal(500),Decimal(100)
    rough=maximum/5
    power=Decimal(10)**(len(str(int(rough)))-1)
    step=next(power*m for m in (Decimal(1),Decimal(2),Decimal('2.5'),Decimal(5),Decimal(10)) if power*m>=rough)
    top=step*(maximum/step).to_integral_value(rounding=ROUND_CEILING)
    return top,step

@roles('admin','accountant')
def finance(request):
    tx=selected_transactions(request)
    income=tx.filter(amount__gt=0).aggregate(v=Sum('amount'))['v'] or Decimal(0)
    expense=-(tx.filter(amount__lt=0).aggregate(v=Sum('amount'))['v'] or Decimal(0))
    monthly=ClientCharge.objects.visible_to(request.user).filter(periodicity='monthly',included=False).aggregate(v=Sum('amount'))['v'] or Decimal(0)
    start=period(request)[0]
    # The phone month stepper: ‹ previous · next ›.
    previous=(start-timedelta(days=1)).replace(day=1)
    following=advance_date(start,'monthly')
    return render(request,'core/finance.html',{'title':'Finanzas','month':start.strftime('%Y-%m'),'month_start':start,'prev_month':previous.strftime('%Y-%m'),'next_month':following.strftime('%Y-%m'),'income':income,'expense':expense,'profit':income-expense,'monthly':monthly,'transactions':tx.select_related('business').order_by('-date'),'expenses':RecurringExpense.objects.visible_to(request.user),'commissions':Commission.objects.visible_to(request.user),'charges':ClientCharge.objects.visible_to(request.user),'chart':chart_data(request.user)})

@roles('admin','accountant')
def export_csv(request):
    output=io.StringIO(); writer=csv.writer(output,delimiter=';')
    writer.writerow(['Fecha','Concepto','Negocio','Categoría','Importe EUR'])
    def safe(text):
        text=str(text or '')
        return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text
    for t in selected_transactions(request).select_related('business').order_by('date'):
        writer.writerow([t.date.isoformat(),safe(t.concept),safe(t.business.name if t.business else ''),t.get_category_display(),str(t.amount).replace('.',',')])
    response=HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="movimientos.csv"'
    return response

@roles('admin')
def finance_create(request,kind):
    forms={'transaction':TransactionForm,'charge':ChargeForm,'expense':ExpenseForm,'commission':CommissionForm}
    if kind not in forms: raise Http404
    form=forms[kind](request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        form.save(); return redirect('finance')
    return render(request,'core/form.html',{'title':{'transaction':'Añadir movimiento','charge':'Añadir cargo','expense':'Gasto fijo','commission':'Comisión'}[kind],'form':form})

@roles('admin','accountant')
def receipt(request,public_id):
    item=scoped(Transaction,request.user,public_id)
    if not item.receipt: raise Http404
    response=FileResponse(item.receipt.open('rb'),as_attachment=True)
    response['Cache-Control']='private, no-store'
    return response

@roles('client')
def portal_charges(request):
    return render(request,'core/portal_charges.html',{'title':'Gastos','charges':ClientCharge.objects.visible_to(request.user).select_related('business')})

@roles('admin')
def finance_edit(request,kind,public_id):
    choices={'transaction':(Transaction,TransactionForm),'charge':(ClientCharge,ChargeForm),'expense':(RecurringExpense,ExpenseForm),'commission':(Commission,CommissionForm)}
    if kind not in choices: raise Http404
    model,form_class=choices[kind]
    item=scoped(model,request.user,public_id)
    form=form_class(request.POST or None,request.FILES or None,instance=item)
    if request.method=='POST' and form.is_valid():
        form.save(); return redirect('finance')
    return render(request,'core/form.html',{'title':'Editar registro económico','form':form})
