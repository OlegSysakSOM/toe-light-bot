"""Mobile-first outage image matching the supplied visual reference."""
from __future__ import annotations
from datetime import datetime, timedelta
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parent
W=600
BG="#070B1C"; PANEL="#151E43"; BORDER="#303B72"
WHITE="#F5F6FF"; MUTED="#9EABD0"; NAVY="#111A3A"
GREEN="#23D98B"; RED="#FF4B63"; YELLOW="#FFC637"; GRAY="#7F89AD"; ORANGE="#FFA326"
GA=(48,98,255); GB=(148,60,241)
WEEKDAYS=("понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя")


@lru_cache(maxsize=32)
def _font(size: int, weight: str="regular") -> ImageFont.ImageFont:
    name="Inter-ExtraBold.otf" if weight=="extra" else ("Inter-Bold.otf" if weight=="bold" else "Inter-Medium.otf")
    path=ROOT/"fonts"/name
    if path.exists(): return ImageFont.truetype(str(path),size=size)
    fallback="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if weight!="regular" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(fallback,size=size)


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int,int]:
    b=draw.textbbox((0,0),text,font=font)
    return b[2]-b[0],b[3]-b[1]


def _fit(draw: ImageDraw.ImageDraw,text: str,width: int,start: int,minimum: int=10,weight: str="bold") -> ImageFont.ImageFont:
    for n in range(start,minimum-1,-1):
        font=_font(n,weight)
        if _measure(draw,text,font)[0]<=width: return font
    return _font(minimum,weight)


def _center(draw: ImageDraw.ImageDraw,x: float,y: float,text: str,font: ImageFont.ImageFont,color: str) -> None:
    w,h=_measure(draw,text,font)
    draw.text((x-w/2,y-h/2-1),text,font=font,fill=color)


def _clock(slot: int) -> str:
    return "24:00" if slot>=48 else f"{slot//2:02d}:{'30' if slot%2 else '00'}"


def _duration(minutes: int) -> str:
    hours,mins=divmod(minutes,60)
    return " ".join(([f"{hours} год"] if hours else [])+([f"{mins} хв"] if mins else [])) or "0 хв"


def _date(day: datetime) -> str:
    return f"{day:%d.%m.%Y} · {WEEKDAYS[day.weekday()]}"


def _color(code: str) -> str:
    return {"0":GREEN,"1":RED,"2":YELLOW}.get(str(code),GRAY)


def _gradient(image: Image.Image,box: tuple[int,int,int,int]) -> None:
    x0,y0,x1,y1=box; w=x1-x0; h=y1-y0
    layer=Image.new("RGB",(w,h)); pixels=layer.load()
    for x in range(w):
        t=x/max(1,w-1); c=tuple(round(GA[i]+(GB[i]-GA[i])*t) for i in range(3))
        for y in range(h): pixels[x,y]=c
    mask=Image.new("L",(w,h),0)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,w-1,h-1),radius=24,fill=255)
    image.paste(layer,(x0,y0),mask)


def _runs(slots: str) -> list[dict[str,Any]]:
    found=[]
    for i,code in enumerate(slots):
        if code not in ("1","2"): continue
        if found and found[-1]["code"]==code and found[-1]["end"]==i: found[-1]["end"]=i+1
        else: found.append({"code":code,"start":i,"end":i+1})
    return found


def _header(image: Image.Image,draw: ImageDraw.ImageDraw,address: str,group: str) -> int:
    x,y,w,h=25,25,550,104
    _gradient(image,(x,y,x+w,y+h))
    draw.ellipse((x+20,y+26,x+70,y+76),fill="#FFFFFF")
    draw.polygon([(x+49,y+32),(x+35,y+53),(x+45,y+53),(x+39,y+69),(x+57,y+47),(x+47,y+47)],fill="#FFB000")
    draw.text((x+86,y+23),"Графік відключень",font=_font(25,"extra"),fill=WHITE)
    draw.text((x+86,y+59),address,font=_fit(draw,address,315,15,11,"regular"),fill="#E8EBFF")
    px,py,pw,ph=x+w-134,y+31,114,42
    draw.rounded_rectangle((px,py,px+pw,py+ph),radius=22,fill="#FFFFFF")
    title=f"Черга {group}"
    _center(draw,px+pw/2,py+ph/2,title,_fit(draw,title,pw-14,16,10,"extra"),"#3564FF")
    return y+h+15


def _placeholder(draw: ImageDraw.ImageDraw,y: int,title: str,day: datetime) -> int:
    x,w,h=25,550,82
    draw.rounded_rectangle((x,y,x+w,y+h),radius=23,fill=PANEL,outline=BORDER,width=1)
    draw.text((x+20,y+17),title,font=_font(25,"extra"),fill=WHITE)
    draw.text((x+20,y+48),_date(day),font=_font(14),fill=MUTED)
    pill="Графік ще не опублікований"; px,py,pw=x+w-273,y+23,253
    draw.rounded_rectangle((px,py,px+pw,py+37),radius=19,fill=GRAY)
    _center(draw,px+pw/2,py+18,pill,_font(13,"bold"),NAVY)
    return y+h


def _day_card(image: Image.Image,draw: ImageDraw.ImageDraw,y: int,key: str,
              schedule: dict[str,Any],checked: datetime) -> int:
    day=datetime.strptime(key,"%Y-%m-%d")
    slots=(str(schedule.get("slots",""))+"3"*48)[:48]
    events=_runs(slots); rows=max(1,(len(events)+1)//2)
    x,w=25,550; grid_y=y+91; th,gx,gy=43,6,7
    grid_h=th*3+gy*2; event_y=grid_y+grid_h+14; ch,cg=34,7
    card_h=event_y-y+rows*ch+(rows-1)*cg+15
    draw.rounded_rectangle((x,y,x+w,y+card_h),radius=23,fill=PANEL,outline=BORDER,width=1)
    title="Сьогодні" if day.date()==checked.date() else "Завтра"
    draw.text((x+20,y+15),title,font=_font(26,"extra"),fill=WHITE)
    draw.text((x+20,y+48),_date(day),font=_font(14),fill=MUTED)
    off,maybe=slots.count("1")*30,slots.count("2")*30
    summary=f"Без світла: {_duration(off)}" if off else (f"Можливе: {_duration(maybe)}" if maybe else "Без відключень")
    sc=RED if off else (YELLOW if maybe else GREEN); sf=_fit(draw,summary,205,14,10,"bold")
    sw=min(218,_measure(draw,summary,sf)[0]+24); sx,sy=x+w-sw-20,y+22
    draw.rounded_rectangle((sx,sy,sx+sw,sy+34),radius=18,fill=sc)
    _center(draw,sx+sw/2,sy+17,summary,sf,NAVY)
    margin,cols=20,8; tw=(w-margin*2-gx*(cols-1))//cols
    current=min(47,checked.hour*2+checked.minute//30) if title=="Сьогодні" else -1
    for hour in range(24):
        row,col=divmod(hour,8); tx=x+margin+col*(tw+gx); ty=grid_y+row*(th+gy)
        a,b=slots[hour*2],slots[hour*2+1]
        tile=Image.new("RGB",(tw,th),_color(a))
        ImageDraw.Draw(tile).rectangle((tw//2,0,tw-1,th-1),fill=_color(b))
        mask=Image.new("L",(tw,th),0)
        ImageDraw.Draw(mask).rounded_rectangle((0,0,tw-1,th-1),radius=12,fill=255)
        image.paste(tile,(tx,ty),mask)
        if hour*2<=current<hour*2+2:
            draw.rounded_rectangle((tx-1,ty-1,tx+tw+1,ty+th+1),radius=13,outline="#FFFFFF",width=2)
            bx,by=tx+tw/2-23,ty-8
            draw.rounded_rectangle((bx,by,bx+46,by+17),radius=9,fill="#FFFFFF")
            _center(draw,bx+23,by+9,"ЗАРАЗ",_font(9,"extra"),NAVY)
        _center(draw,tx+tw/2,ty+th/2,f"{hour:02d}–{hour+1:02d}",_font(15,"extra"),NAVY)
    if not events:
        label="За графіком відключень немає"
        draw.rounded_rectangle((x+20,event_y,x+w-20,event_y+ch),radius=18,fill=GREEN)
        _center(draw,x+w/2,event_y+ch/2,label,_font(14,"bold"),NAVY)
    else:
        cw=(w-40-cg)//2
        for i,e in enumerate(events):
            row,col=divmod(i,2); px=x+20+col*(cw+cg); py=event_y+row*(ch+cg)
            mins=(e["end"]-e["start"])*30
            label=f"{_clock(e['start'])}–{_clock(e['end'])} · {_duration(mins)}"
            draw.rounded_rectangle((px,py,px+cw,py+ch),radius=18,fill=RED if e["code"]=="1" else YELLOW)
            _center(draw,px+cw/2,py+ch/2,label,_fit(draw,label,cw-14,14,10,"bold"),NAVY)
    return y+card_h


def _footer(draw: ImageDraw.ImageDraw,y: int,checked: datetime) -> None:
    x,w,h=25,550,129
    draw.rounded_rectangle((x,y,x+w,y+h),radius=23,fill=PANEL,outline=ORANGE,width=2)
    cx,cy=x+38,y+33
    draw.ellipse((cx-18,cy-18,cx+18,cy+18),fill=ORANGE)
    _center(draw,cx,cy+1,"!",_font(25,"extra"),NAVY)
    draw.text((x+69,y+12),"Графік оновлено",font=_font(22,"extra"),fill=WHITE)
    stamp=f"Оновлено о {checked:%H:%M} · перевірка кожні 10 хв"
    draw.text((x+69,y+39),stamp,font=_fit(draw,stamp,455,13,10,"regular"),fill=MUTED)
    for color,label,dx in ((GREEN,"Є світло",45),(RED,"Немає світла",153),(YELLOW,"Можливе відключення",300)):
        lx=x+dx; draw.rounded_rectangle((lx,y+82,lx+12,y+94),radius=4,fill=color)
        draw.text((lx+18,y+79),label,font=_font(12,"bold"),fill=WHITE)
    draw.text((x+20,y+109),"Кожна плитка — година; половинки — по 30 хв",font=_font(11),fill=MUTED)


def render_schedule_png(days: dict[str,dict[str,Any]],address_label: str,
                        group: str,checked_at: datetime) -> bytes:
    if not days: raise ValueError("Немає днів для побудови зображення")
    today=checked_at.date(); tomorrow=today+timedelta(days=1)
    records={str(day)[:10]:item for day,item in days.items()}
    image=Image.new("RGB",(W,8000),BG); draw=ImageDraw.Draw(image)
    y=_header(image,draw,address_label,group)
    for key,title,day in ((today.isoformat(),"Сьогодні",today),(tomorrow.isoformat(),"Завтра",tomorrow)):
        if key in records: y=_day_card(image,draw,y,key,records[key],checked_at)
        else: y=_placeholder(draw,y,title,datetime.combine(day,datetime.min.time()))
        y+=14
    _footer(draw,y,checked_at)
    output=BytesIO()
    image.crop((0,0,W,y+149)).save(output,format="PNG",optimize=True)
    return output.getvalue()
