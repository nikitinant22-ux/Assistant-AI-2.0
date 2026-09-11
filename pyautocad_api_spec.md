ЭТАЛОННАЯ СПЕЦИФИКАЦИЯ МЕТОДОВ PYAUTOCAD ДЛЯ ИИ-АССИСТЕНТА
БЛОК 1: БАЗОВАЯ И СЛОЖНАЯ ГЕОМЕТРИЯ (Черчение)
•	Отрезок (Линия): acad.model.AddLine(APoint(x1, y1, z1), APoint(x2, y2, z2))
o	Возвращает объект класса: IAcadLine
•	Окружность (Круг): acad.model.AddCircle(APoint(x, y, z), float(radius))
o	Возвращает объект класса: IAcadCircle
•	Легкая полилиния (LWPolyline): acad.model.AddLightWeightPolyline(double_array)
o	Примечание: Принимает одномерный плоский массив координат [x1, y1, x2, y2...].
o	Возвращает объект класса: IAcadLWPolyline
•	3D Полилиния: acad.model.Add3DPoly(double_array)
o	Примечание: Принимает одномерный массив координат по 3 точки [x1, y1, z1, x2, y2, z2...].
o	Возвращает объект класса: IAcad3DPolyline
•	Дуга (Arc): acad.model.AddArc(APoint(x, y, z), float(radius), float(start_angle_rad), float(end_angle_rad))
o	Возвращает объект класса: IAcadArc
•	Эллипс: acad.model.AddEllipse(APoint(x, y, z), APoint(major_axis_x, major_axis_y, major_axis_z), float(radius_ratio))
o	Возвращает объект класса: IAcadEllipse
•	Сплайн (Spline): acad.model.AddSpline(points_array, start_tangent_vector, end_tangent_vector)
o	Возвращает объект класса: IAcadSpline
•	Точка (Point): acad.model.AddPoint(APoint(x, y, z))
o	Возвращает объект класса: IAcadPoint
•	Прямая (XLine): acad.model.AddXline(APoint(x1, y1, z1), APoint(x2, y2, z2))
o	Возвращает объект класса: IAcadXline
•	Луч (Ray): acad.model.AddRay(APoint(x1, y1, z1), APoint(x2, y2, z2))
o	Возвращает объект класса: IAcadRay
•	Штриховка (Hatch): acad.model.AddHatch(int(pattern_type), str(pattern_name), bool(associativity))
o	Возвращает объект класса: IAcadHatch
•	Прямоугольник (Через LWPolyline): Алгоритм формирования массива 4-х точек [x1, y1, x2, y1, x2, y2, x1, y2] и вызов AddLightWeightPolyline с последующим .Closed = True.
БЛОК 2: АННОТАЦИИ, ТЕКСТЫ И РАЗМЕРЫ (Оформление)
•	Однострочный текст: acad.model.AddText(str(text), APoint(x, y, z), float(height))
o	Возвращает объект класса: IAcadText
•	Многострочный текст (МТекст): acad.model.AddMText(APoint(x, y, z), float(width), str(text))
o	Возвращает объект класса: IAcadMText
•	Размер линейный/повернутый: acad.model.AddDimRotated(APoint(ext1_x, ext1_y), APoint(ext2_x, ext2_y), APoint(line_x, line_y), float(angle_rad))
o	Возвращает объект класса: IAcadDimRotated
•	Размер параллельный: acad.model.AddDimAligned(APoint(ext1_x, ext1_y), APoint(ext2_x, ext2_y), APoint(text_x, text_y))
o	Возвращает объект класса: IAcadDimAligned
•	Размер радиуса: acad.model.AddDimRadial(APoint(center_x, center_y), APoint(chord_x, chord_y), float(leader_len))
o	Возвращает объект класса: IAcadDimRadial
•	Размер диаметра: acad.model.AddDimDiametric(APoint(chord1_x, chord1_y), APoint(chord2_x, chord2_y), float(leader_len))
o	Возвращает объект класса: IAcadDimDiametric
•	Классическая выноска (Leader): acad.model.AddLeader(points_array, annotation_object, int(leader_type))
o	Возвращает объект класса: IAcadLeader
•	Мультивыноска (MLeader): acad.model.AddMLeader(points_array, int(leader_line_index))
o	Возвращает объект класса: IAcadMLeader
•	Таблица (Table): acad.model.AddTable(APoint(x, y, z), int(num_rows), int(num_cols), float(row_height), float(col_width))
o	Возвращает объект класса: IAcadTable
БЛОК 3: КОМПОНЕНТЫ И ВНЕШНИЕ ССЫЛКИ (Блоки)
•	Вставка блока (Инсерт): acad.model.InsertBlock(APoint(x, y, z), str(block_name), float(sx), float(sy), float(sz), float(rotation_rad))
o	Возвращает объект класса: IAcadBlockReference
•	Вставка внешней ссылки (XRef): acad.model.AttachExternalReference(str(file_path), str(block_name), APoint(x, y, z), float(sx), float(sy), float(sz), float(rotation_rad), bool(b_overlay))
o	Возвращает объект класса: IAcadExternalReference
•	Атрибуты блока: Метод .GetAttributes() вызывается у объекта IAcadBlockReference. Возвращает коллекцию IAcadAttributeReference. Ключевые свойства: .TagString (тег), .TextString (значение).
•	Динамические свойства блока: Метод .GetDynamicBlockProperties() у объекта IAcadBlockReference. Возвращает коллекцию IAcadDynamicBlockReferenceProperty. Ключевые свойства: .PropertyName, .Value.
БЛОК 4: ИНСПЕКЦИЯ, СТРУКТУРА И МЕТАДАННЫЕ (Аналитика)
•	Чтение имени и пути файла: acad.doc.Name (имя вкладки), acad.doc.Path (каталог), acad.doc.FullName (полный путь к DWG).
•	Список открытых вкладок: Цикл перебора по коллекции документов: [d.Name for d in acad.app.Documents].
•	Список листов (Layouts): Цикл перебора коллекции листов: [ly.Name for ly in acad.doc.Layouts if ly.Name != "Model"].
•	Список слоев: Цикл перебора коллекции слоев: [layer.Name for layer in acad.doc.Layers].
•	Свойства слоя: У объекта IAcadLayer: .LayerOn (видимость), .Freeze (заморозка), .Lock (блокировка), .Color (числовой индекс цвета ACI).
•	Сводная статистика объектов чертежа: Итерация по пространству модели for obj in acad.model: с чтением свойства obj.ObjectName (возвращает внутренний тип, например, AcDbCircle, AcDbLine).
•	Габариты объекта (Bounding Box): Метод obj.GetBoundingBox(min_pt, max_pt). Заполняет переменные min_pt и max_pt крайними точками координат углов объекта.
•	Геометрические свойства примитивов (Чтение/Запись):
o	Линия: .StartPoint, .EndPoint, .Length, .Angle.
o	Круг: .Center, .Radius, .Diameter, .Area.
o	Полилиния: .Coordinates (список вершин), .Closed (замкнутость), .Area, .Length.
БЛОК 5: НАВИГАЦИЯ, УПРАВЛЕНИЕ СРЕДОЙ И СВОЙСТВАМИ (Оператор)
•	Переключение пространств: acad.doc.ActiveSpace = 1 (Переход в Модель), acad.doc.ActiveSpace = 0 (Переход на текущий Лист).
•	Активация конкретного листа: acad.doc.ActiveLayout = acad.doc.Layouts.Item(str(sheet_name)).
•	Активация другой открытой вкладки чертежа: acad.app.Documents.Item(str(drawing_name)).Activate().
•	Поиск объекта по его паспорту: acad.doc.HandleToObject(str(handle)) — возвращает сам объект IAcadEntity.
•	Изменение цвета объекта: obj.Color = int(aci_index).
•	Перенос объекта на другой слой: obj.Layer = str(layer_name).
•	Удаление объекта: obj.Delete().
•	Системные переменные ( doc.GetVariable / doc.SetVariable ):
o	INSUNITS — единицы чертежа (4 - мм, 6 - метры).
o	CLAYER — имя текущего активного слоя.
•	Регенерация экрана: acad.doc.Regen(1) (полная принудительная перерисовка графического окна)
