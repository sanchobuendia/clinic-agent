from services.patient_registry import (
    PatientRegistryError,
    create_patient,
    get_patient_by_cpf,
)
from state import GraphState, RegistryResult
from utils.logger import get_logger

logger = get_logger("RegistryAgent")


async def registry_agent(state: GraphState) -> dict:
    decision = state["router_decision"]
    query = state["user_query"].lower()

    fields_updated: list[str] = []
    if "telefone" in query:
        fields_updated.append("telefone")
    if "convenio" in query:
        fields_updated.append("convenio")
    if "documento" in query:
        fields_updated.append("documentos")

    if not fields_updated:
        fields_updated.append("status_cadastral")

    summary = "Cadastro localizado e pronto para atualizacao administrativa."
    status = "ready"
    patient_exists = True
    patient_id = None
    patient_cpf = decision.patient_cpf if decision else None
    patient_name = decision.patient_name if decision else None
    patient_age = decision.patient_age if decision else None
    patient_sex = decision.patient_sex if decision else None
    patient_email = decision.patient_email if decision else None
    patient_phone = decision.patient_phone if decision else None

    if decision and decision.requested_action == "agendar":
        if not decision.patient_cpf:
            summary = "Antes de verificar seu cadastro, preciso do CPF."
            status = "awaiting_cpf"
            patient_exists = False
        else:
            try:
                patient = await get_patient_by_cpf(decision.patient_cpf)
            except PatientRegistryError as exc:
                summary = f"Cadastro indisponivel: {exc}"
                status = "integration_error"
                patient_exists = False
                result = RegistryResult(
                    status=status,
                    patient_exists=patient_exists,
                    patient_id=patient_id,
                    patient_cpf=patient_cpf,
                    patient_name=patient_name,
                    patient_age=patient_age,
                    patient_sex=patient_sex,
                    patient_email=patient_email,
                    patient_phone=patient_phone,
                    fields_updated=fields_updated,
                    summary=summary,
                )
                logger.warning("Falha na integracao de cadastro: %s", exc)
                return {"registry_result": result, "error_log": state.get("error_log", []) + [summary]}

            if patient:
                patient_id = patient.id
                patient_exists = True
                patient_cpf = patient.cpf
                patient_name = patient.full_name
                patient_age = patient.age
                patient_sex = patient.sex
                patient_email = patient.email
                patient_phone = patient.phone
                summary = f"Cadastro localizado para CPF {patient.cpf}. Prosseguindo com o agendamento."
                status = "found"
            else:
                patient_exists = False
                missing_fields: list[str] = []
                if not decision.patient_name:
                    missing_fields.append("nome completo")
                if decision.patient_age is None:
                    missing_fields.append("idade")
                if not decision.patient_sex:
                    missing_fields.append("sexo")
                if not decision.patient_email:
                    missing_fields.append("email")
                if not decision.patient_phone:
                    missing_fields.append("celular")

                if missing_fields:
                    summary = (
                        f"CPF nao localizado na base. Para criar o cadastro, preciso de {', '.join(missing_fields)}."
                    )
                    status = "awaiting_patient_data"
                else:
                    try:
                        created_patient = await create_patient(
                            cpf=decision.patient_cpf,
                            full_name=decision.patient_name,
                            age=decision.patient_age,
                            sex=decision.patient_sex,
                            email=decision.patient_email,
                            phone=decision.patient_phone,
                        )
                    except PatientRegistryError as exc:
                        summary = f"Cadastro indisponivel: {exc}"
                        status = "integration_error"
                        result = RegistryResult(
                            status=status,
                            patient_exists=patient_exists,
                            patient_id=patient_id,
                            patient_cpf=patient_cpf,
                            patient_name=patient_name,
                            patient_age=patient_age,
                            patient_sex=patient_sex,
                            patient_email=patient_email,
                            patient_phone=patient_phone,
                            fields_updated=fields_updated,
                            summary=summary,
                        )
                        logger.warning("Falha ao criar cadastro: %s", exc)
                        return {"registry_result": result, "error_log": state.get("error_log", []) + [summary]}
                    patient_id = created_patient.id
                    patient_exists = False
                    patient_cpf = created_patient.cpf
                    patient_name = created_patient.full_name
                    patient_age = created_patient.age
                    patient_sex = created_patient.sex
                    patient_email = created_patient.email
                    patient_phone = created_patient.phone
                    fields_updated = ["cpf", "nome", "idade", "sexo", "email", "celular"]
                    summary = f"Cadastro criado para {created_patient.full_name}. Prosseguindo com o agendamento."
                    status = "created"

    if decision and decision.requested_action == "cadastro":
        summary = f"Solicitacao de cadastro processada. Campos envolvidos: {', '.join(fields_updated)}."
        status = "ready"

    result = RegistryResult(
        status=status,
        patient_exists=patient_exists,
        patient_id=patient_id,
        patient_cpf=patient_cpf,
        patient_name=patient_name,
        patient_age=patient_age,
        patient_sex=patient_sex,
        patient_email=patient_email,
        patient_phone=patient_phone,
        fields_updated=fields_updated,
        summary=summary,
    )
    logger.info("Cadastro processado.")
    updates: dict = {"registry_result": result}
    if decision and patient_cpf:
        updates["router_decision"] = decision.model_copy(
            update={
                "patient_cpf": patient_cpf,
                "patient_name": patient_name,
                "patient_age": patient_age,
                "patient_sex": patient_sex,
                "patient_email": patient_email,
                "patient_phone": patient_phone,
            }
        )
    return updates
