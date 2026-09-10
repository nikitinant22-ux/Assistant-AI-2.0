import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REF_FILE = os.path.join(BASE_DIR, "cad_reference.md")

def parse_md_into_blocks():
    """Разбивает гигантский справочник на изолированные объекты САПР"""
    if not os.path.exists(REF_FILE):
        return {}
        
    try:
        with open(REF_FILE, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Ищем блоки вида [OBJECT: IAcadName]
        chunks = re.split(r'(\[OBJECT:\s+\w+\])', content)
        if len(chunks) < 2:
            return {"global": content}
            
        blocks = {}
        # Первый кусок — вводный заголовок справочника
        blocks["global_header"] = chunks[0].strip()
        
        # Собираем пары: Заголовок объекта -> Его методы и свойства
        for i in range(1, len(chunks), 2):
            header = chunks[i].strip()
            # Извлекаем чистое имя (например: IAcadModelSpace)
            obj_name = header.replace("[OBJECT:", "").replace("]", "").strip()
            
            body = chunks[i+1] if (i+1) < len(chunks) else ""
            blocks[obj_name] = (header + "\n" + body).strip()
            
        return blocks
    except:
        return {}

def get_relevant_blocks(user_query, max_blocks=3):
    """Высокоскоростной семантический поиск релевантных блоков спецификации"""
    blocks = parse_md_into_blocks()
    if not blocks:
        return ""
        
    query_words = set(re.findall(r'\w+', user_query.lower()))
    if not query_words:
        return ""
        
    scored_blocks = []
    global_header = blocks.pop("global_header", "")
    
    # Расчет весов пересечений токенов (TF-IDF семантический срез)
    for obj_name, block_content in blocks.items():
        block_lower = block_content.lower()
        score = 0
        
        # Сильный вес, если имя объекта прямо упомянуто в промпте
        # Убираем системную букву 'I' в начале для точного сопоставления
        clean_name = obj_name[1:] if obj_name.startswith('I') else obj_name
        if clean_name.lower() in user_query.lower():
            score += 50
            
        # Вес за синонимы и контекстные пересечения слов
        for word in query_words:
            # Исключаем короткие союзы и предлоги
            if len(word) < 3: continue
            
            # Прямые вхождения
            if word in block_lower:
                score += 10
                
            # ИНЖЕНЕРНЫЙ СЛОВАРЬ СИНОНИМОВ (Связывает русский промпт с COM-интерфейсами Autodesk)
            synonyms = {
                "слой": ["layer", "layers"], 
                "слоев": ["layer", "layers"], 
                "назван": ["name"],
                "круг": ["circle"], 
                "окружн": ["circle"], 
                "линия": ["line"], 
                "отрез": ["line"],
                "полилин": ["polyline", "lwpolyline"], 
                "текст": ["text", "mtext"], 
                "зум": ["zoom", "viewport"],
                "показ": ["zoom", "viewport", "extents"], 
                "таблиц": ["table"], 
                "вынос": ["leader", "mleader"],
                "цвет": ["color", "accmcolor"], 
                "удалит": ["delete", "erase", "purge"],
                
                # ТОЧЕЧНОЕ ИСПРАВЛЕНИЕ: Связываем листы и вкладки с Layout-моделью AutoCAD
                "лист": ["layout", "layouts", "activelayout"],
                "открыт": ["documents", "document", "activedocument", "name", "fullname"],
                "чертеж": ["documents", "document", "activedocument", "name", "fullname"],
                "листе": ["layout", "layouts", "activelayout"],
                "вкладк": ["layout", "layouts", "activelayout", "document"],
                "перейд": ["layout", "layouts", "activelayout", "item"],
                "откры": ["layout", "layouts", "activelayout", "document"],
                "пространств": ["modelspace", "paperspace", "layout"],
                
                # Перспективный задел под блоки и спецификации
                "блок": ["block", "blocks", "insert", "dynamicblock"],
                "штамп": ["block", "blocks", "attribute"],
                "атриб": ["attribute", "attributes", "getattributes"],
                
                # Типы объектов / примитивы
                "тип объект": ["objectname", "entityname", "object", "modelspace"],
                "тип примитив": ["objectname", "entityname", "object", "modelspace"],
                "примитив": ["objectname", "entityname", "polyline", "circle", "line"],
                "объект": ["objectname", "entityname", "modelspace", "count"],
                
                # Свойства объекта (инспекция)
                "свойств": ["layer", "color", "coordinates", "length", "area", "linetype", "visible"],
                "характеристик": ["layer", "color", "length", "area"],
                "площадь": ["area", "boundingbox"],
                "длина": ["length", "distance", "coordinates"],
                "координат": ["coordinates", "point", "startpoint", "endpoint"],
                
                # Поиск и подсчёт
                "найди": ["modelspace", "count", "item", "select", "selection"],
                "найти": ["modelspace", "count", "item", "select", "selection"],
                "поиск": ["modelspace", "count", "item", "select", "selection"],
                "сколько": ["count", "modelspace"],
                "подсчитай": ["count", "modelspace"],
                "выдели": ["selection", "selectionselect", "pickfirst", "highlight"],
                
                # Изменение объектов
                "измен": ["move", "rotate", "scaleentity", "layer", "color", "update", "delete"],
                "смен": ["layer", "color", "update"],
                "передвин": ["move", "translate"],
                "поверн": ["rotate"],
                "разверн": ["rotate"],
                "масштаб": ["scaleentity"],
                "удали": ["delete", "erase", "purge"],
                "скрой": ["visible", "hide"],
                "спряч": ["visible", "hide"],
                "слоёв": ["layers", "layer"],
                "лист": ["layout", "layouts", "activelayout"]
            }
            
            for root, syn_list in synonyms.items():
                if root in word:
                    for syn in syn_list:
                        if syn in block_lower: 
                            score += 15

        if score > 0:
            scored_blocks.append((score, block_content))
            
    # Сортируем по убыванию веса релевантности
    scored_blocks.sort(key=lambda x: x[0], reverse=True)
    
    # Собираем итоговую точечную выжимку знаний
    selected_content = [global_header]
    for _, content in scored_blocks[:max_blocks]:
        selected_content.append(content)
        
    return "\n\n".join(selected_content)
