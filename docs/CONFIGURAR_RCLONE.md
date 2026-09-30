# Configurar el acceso a GloH2O desde GitHub Actions

Este procedimiento se hace **una sola vez**, en tu computador. El resultado es un
secreto de GitHub que le permite al pipeline leer las carpetas compartidas de
GloH2O sin intervención humana.

> **Por qué no se puede usar una cuenta de servicio**
> GloH2O comparte las carpetas con una cuenta de Google concreta (la que usaste
> al solicitar el acceso). Tú no eres dueño de esas carpetas, así que no puedes
> volver a compartirlas con una cuenta de servicio. La única vía es autenticar
> rclone con la cuenta de usuario que recibió el acceso.

---

## Paso 1. Crear credenciales propias de Google (no opcional)

rclone trae un `client_id` compartido entre todos sus usuarios del mundo, y
Google lo tiene fuertemente limitado. Con él las descargas van lentas y
aparecen errores de cuota. Hay que crear credenciales propias.

1. Entra a <https://console.cloud.google.com/> con **la cuenta del proyecto**
   (la misma que recibió el acceso de GloH2O).
2. Crea un proyecto nuevo, por ejemplo `pma-clima`.
3. Ve a **APIs y servicios → Biblioteca**, busca **Google Drive API** y actívala.
4. Ve a **APIs y servicios → Pantalla de consentimiento de OAuth**:
   - Tipo de usuario: **Externo**.
   - Llena nombre de la app, correo de soporte y correo del desarrollador.
   - En **Permisos**, agrega el scope `.../auth/drive.readonly`.
   - **IMPORTANTE:** al terminar, pulsa **PUBLICAR APLICACIÓN** para pasar del
     estado *Prueba* al estado *En producción*.

   > ### La trampa de los 7 días
   > Mientras la pantalla de consentimiento esté en estado **Prueba**, Google
   > invalida el *refresh token* a los 7 días. El pipeline funcionaría una
   > semana y luego empezaría a fallar con `invalid_grant` sin razón aparente.
   > Publicar la aplicación evita esto. No requiere verificación de Google
   > mientras solo la use tu propia cuenta.

5. Ve a **Credenciales → Crear credenciales → ID de cliente de OAuth**:
   - Tipo de aplicación: **Aplicación de escritorio**.
   - Guarda el **ID de cliente** y el **secreto de cliente**.

---

## Paso 2. Autenticar rclone localmente

Instala rclone desde <https://rclone.org/downloads/> y ejecuta:

```bash
rclone config
```

Responde:

| Pregunta | Respuesta |
|---|---|
| `n/s/q` | `n` (new remote) |
| name | `gloh2o` |
| Storage | `drive` |
| client_id | el del Paso 1 |
| client_secret | el del Paso 1 |
| scope | `2` (`drive.readonly`) |
| service_account_file | *(vacío)* |
| Edit advanced config | `n` |
| Use web browser to automatically authenticate | `y` |
| Configure this as a Shared Drive | `n` |

Se abrirá el navegador. **Autoriza con la cuenta que recibió el acceso de
GloH2O.** Si Google muestra una advertencia de app no verificada, entra por
*Configuración avanzada → Ir a (nombre de la app)*.

---

## Paso 3. Verificar que ves las carpetas

```bash
rclone lsd gloh2o: --drive-shared-with-me
```

Debes ver las carpetas que GloH2O compartió (MSWEP V2.8, MSWEP V3.16 y MSWX).
Si la lista sale vacía:

- Confirma que autenticaste con la cuenta correcta.
- Abre <https://drive.google.com/drive/shared-with-me> y verifica que las
  carpetas aparezcan ahí.
- El flag `--drive-shared-with-me` es obligatorio: sin él rclone solo mira
  *Mi unidad* y no encuentra nada.

---

## Paso 4. Convertir la configuración en un secreto de GitHub

```bash
# Ver dónde quedó el archivo
rclone config file

# Codificarlo en base64 (una sola línea)
base64 -w 0 "$(rclone config file | tail -1)" > rclone_conf_b64.txt
```

En macOS usa `base64 -i archivo -o salida.txt` (no existe `-w 0`).

Luego, en el repositorio de GitHub:

**Settings → Secrets and variables → Actions → New repository secret**

- Nombre: `RCLONE_CONFIG_B64`
- Valor: el contenido completo de `rclone_conf_b64.txt`

Borra `rclone_conf_b64.txt` de tu computador cuando termines.

> El archivo contiene un token de acceso a tu Drive. Nunca lo subas al
> repositorio ni lo pegues en un chat.

---

## Paso 5. Ejecutar el workflow de verificación

En GitHub: pestaña **Actions → Verificar acceso a GloH2O → Run workflow**.

El workflow lista las carpetas compartidas y explora su estructura. Su salida
es el insumo para configurar las rutas reales del pipeline.

---

## Mantenimiento

| Situación | Qué hacer |
|---|---|
| `invalid_grant` a la semana | La pantalla de consentimiento quedó en *Prueba*. Publícala y repite los pasos 2 a 4. |
| `invalid_grant` tras meses sin correr | Google revoca refresh tokens sin uso durante ~6 meses. El pipeline diario lo mantiene vivo. |
| Error 403 de cuota de descarga | Cuota del archivo compartido de GloH2O. Reintentar más tarde; no es problema de configuración. |
| Entrega al PMA | Ellos repiten los pasos 1 a 4 con su propia cuenta, tras solicitar su acceso a GloH2O. El secreto se reemplaza y nadie comparte contraseñas. |
